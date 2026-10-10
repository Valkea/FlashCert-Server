"""Storage. SQLAlchemy Core keeps the SQL portable: SQLite by default, PostgreSQL by changing the URL."""

from __future__ import annotations

import hashlib
import json
import secrets
import time

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    func,
    inspect,
    or_,
    select,
    text,
)
from sqlalchemy.engine import Engine

SCHEMA_VERSION = 3

metadata = MetaData()

meta = Table(
    "meta",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
)

#: Sync credentials. Only a SHA-256 of each token is kept; the token itself is shown once. A revoked token keeps
#: its row, so its id is never handed to a new token that would then own its courses.
token = Table(
    "token",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("label", String(100), nullable=False, default=""),
    Column("hash", String(64), nullable=False, unique=True),
    Column("created_at", BigInteger, nullable=False),
    Column("last_used_at", BigInteger),
    Column("revoked_at", BigInteger),
)

course = Table(
    "course",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    #: The app's own identifier for the course; syncing the same uid again replaces the content.
    Column("uid", String(100), nullable=False, unique=True),
    #: Random, unguessable id in the public URL (/c/<share_id>).
    Column("share_id", String(16), nullable=False, unique=True),
    Column("title", String(300), nullable=False),
    Column("certification", String(200)),
    Column("lang", String(16)),
    #: Order inside its certification (the app's own course order); title order when missing.
    Column("position", BigInteger),
    #: The token that sent it: the only one that can change, download or delete it. None for courses sent before
    #: owners existed (the next token that sends one claims it) or imported from the command line.
    Column("owner_id", Integer),
    #: "public": on the course list. "unlisted": reachable only through its link. "private": a backup, no page.
    Column("visibility", String(10), nullable=False, default="public"),
    #: The normalised course document (notes, sheet, questions, cards) as JSON.
    Column("content", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("question_count", Integer, nullable=False, default=0),
    Column("card_count", Integer, nullable=False, default=0),
    Column("created_at", BigInteger, nullable=False),
    #: Last time the content changed (a re-sync of identical content leaves it alone).
    Column("updated_at", BigInteger, nullable=False),
    #: Informational device edit time; monotonic revisions, not device clocks, guard concurrent changes.
    Column("modified_at", BigInteger),
    Column("revision", BigInteger, nullable=False, default=1),
    Column("deleted_at", BigInteger),
    Column("event", String(16), nullable=False, default="created"),
)

#: Courses deleted through the sync API, so the owner's other devices learn about it. reason: "deleted" (the
#: course was deleted on a device) or "removed" (taken off the server, still on the device).
course_gone = Table(
    "course_gone",
    metadata,
    Column("uid", String(100), primary_key=True),
    Column("owner_id", Integer),
    Column("reason", String(10), nullable=False),
    Column("deleted_at", BigInteger, nullable=False),
    Column("revision", BigInteger, nullable=False, default=0),
)

course_revision = Table(
    "course_revision", metadata,
    Column("course_id", Integer, ForeignKey("course.id", ondelete="CASCADE"), primary_key=True),
    Column("revision", BigInteger, primary_key=True),
    Column("snapshot", Text, nullable=False),
    Column("action", String(16), nullable=False),
    Column("device", String(100), nullable=False, default=""),
    Column("created_at", BigInteger, nullable=False),
)

#: Errors flagged by visitors on one element of a course.
report = Table(
    "report",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("course_id", Integer, ForeignKey("course.id", ondelete="CASCADE"), nullable=False),
    #: question | card | section | sheet | course
    Column("element_kind", String(16), nullable=False),
    Column("element_id", String(100), nullable=False, default=""),
    #: What the element said when it was flagged, so the report still reads well after a re-sync.
    Column("excerpt", Text, nullable=False, default=""),
    Column("message", Text, nullable=False),
    Column("author", String(80), nullable=False, default=""),
    #: open | resolved | dismissed
    Column("status", String(10), nullable=False, default="open"),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)

Index("report_course_status", report.c.course_id, report.c.status)
Index("course_visibility", course.c.visibility, course.c.updated_at)
course_owner = Index("course_owner", course.c.owner_id)


def now_ms() -> int:
    return int(time.time() * 1000)


def make_engine(url: str) -> Engine:
    engine = create_engine(url, future=True, pool_pre_ping=not url.startswith("sqlite"))
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if ":memory:" not in url:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    return engine


def init(engine: Engine) -> None:
    """Creates missing tables, then brings an older database up to SCHEMA_VERSION."""
    metadata.create_all(engine)
    with engine.begin() as conn:
        row = conn.execute(select(meta.c.value).where(meta.c.key == "schema_version")).first()
        if row is None:
            conn.execute(meta.insert().values(key="schema_version", value=str(SCHEMA_VERSION)))
            return
        version = int(row.value)
        if version < 2:
            _to_v2(conn)
        if version < 3:
            _to_v3(conn)
        if version < SCHEMA_VERSION:
            conn.execute(meta.update().where(meta.c.key == "schema_version").values(value=str(SCHEMA_VERSION)))


def _to_v2(conn) -> None:
    """Owners, edit times and revoked tokens. With a single token, every course already there is its own."""
    conn.execute(text("ALTER TABLE token ADD COLUMN revoked_at BIGINT"))
    conn.execute(text("ALTER TABLE course ADD COLUMN owner_id INTEGER"))
    conn.execute(text("ALTER TABLE course ADD COLUMN modified_at BIGINT"))
    course_owner.create(conn, checkfirst=True)
    tokens = conn.execute(select(token.c.id)).scalars().all()
    if len(tokens) == 1:
        conn.execute(course.update().values(owner_id=tokens[0]))


def _to_v3(conn) -> None:
    for table, columns in (("course", {"revision": "BIGINT NOT NULL DEFAULT 1", "deleted_at": "BIGINT",
                                        "event": "VARCHAR(16) NOT NULL DEFAULT 'created'"}),
                           ("course_gone", {"revision": "BIGINT NOT NULL DEFAULT 0"})):
        present = {c["name"] for c in inspect(conn).get_columns(table)}
        for name, definition in columns.items():
            if name not in present:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
    for row in conn.execute(select(course)).all():
        _snapshot(conn, row, "migrated", "")


# --- Tokens -------------------------------------------------------------------------------------


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_token(engine: Engine, label: str = "") -> tuple[int, str]:
    raw = "fcs_" + secrets.token_urlsafe(32)
    with engine.begin() as conn:
        result = conn.execute(
            token.insert().values(label=label[:100], hash=hash_token(raw), created_at=now_ms())
        )
        return result.inserted_primary_key[0], raw


def check_token(engine: Engine, raw: str) -> int | None:
    """The id of the token, or None when it is unknown or revoked."""
    if not raw:
        return None
    digest = hash_token(raw)
    with engine.begin() as conn:
        row = conn.execute(select(token.c.id, token.c.last_used_at)
                           .where(token.c.hash == digest, token.c.revoked_at.is_(None))).first()
        if row is None:
            return None
        # One write a minute at most: the app syncs in bursts.
        if row.last_used_at is None or now_ms() - row.last_used_at > 60_000:
            conn.execute(token.update().where(token.c.id == row.id).values(last_used_at=now_ms()))
        return row.id


def list_tokens(engine: Engine) -> list[dict]:
    """Every token, revoked ones included, with the number of courses it owns."""
    owned = (select(course.c.owner_id, func.count().label("courses"))
             .where(course.c.deleted_at.is_(None)).group_by(course.c.owner_id)).subquery()
    with engine.connect() as conn:
        rows = conn.execute(select(token.c.id, token.c.label, token.c.created_at, token.c.last_used_at,
                                   token.c.revoked_at, func.coalesce(owned.c.courses, 0).label("courses"))
                            .outerjoin(owned, owned.c.owner_id == token.c.id)
                            .order_by(token.c.id))
        return [dict(r._mapping) for r in rows]


def revoke_token(engine: Engine, token_id: int) -> bool:
    with engine.begin() as conn:
        return conn.execute(token.update().where(token.c.id == token_id, token.c.revoked_at.is_(None))
                            .values(revoked_at=now_ms())).rowcount > 0


def token_count(engine: Engine) -> int:
    """Tokens still in use."""
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(token).where(token.c.revoked_at.is_(None))).scalar_one()


# --- Courses ------------------------------------------------------------------------------------


class NotYours(Exception):
    """The course belongs to another token."""


class SyncConflict(Exception):
    def __init__(self, code: str, revision: int = 0, **details):
        self.code, self.revision, self.details = code, revision, details
        super().__init__(code.replace("_", " "))


def _snapshot(conn, row, action: str, device: str) -> None:
    conn.execute(course_revision.insert().values(course_id=row.id, revision=row.revision,
                 snapshot=json.dumps(dict(row._mapping), ensure_ascii=False), action=action,
                 device=device[:100], created_at=now_ms()))


def _expected(row, expected: int | None) -> None:
    if expected is None:
        raise SyncConflict("revision_required", row.revision)
    if expected != row.revision:
        raise SyncConflict("revision_conflict", row.revision)


def _update(conn, row, values: dict, action: str, device: str) -> None:
    owner = course.c.owner_id.is_(None) if row.owner_id is None else course.c.owner_id == row.owner_id
    done = conn.execute(course.update().where(course.c.id == row.id, course.c.revision == row.revision, owner)
                        .values(**values, revision=row.revision + 1, event=action)).rowcount
    if done != 1:
        raise SyncConflict("revision_conflict", row.revision)
    _snapshot(conn, conn.execute(select(course).where(course.c.id == row.id)).one(), action, device)


def save_course(engine: Engine, uid: str, values: dict, owner_id: int | None, *, expected: int | None = None,
                confirmation: str | None = None, device: str = "", notes_limit: int = 30,
                items_limit: int = 20) -> tuple[bool, bool]:
    """Creates or replaces course ``uid``; returns (created, changed).

    ``values`` are the columns taken from the document: the meta, content, content_hash, counts and modified_at.
    A token can only send its own courses and claims one without an owner. Deleted courses require explicit
    restore. ``owner_id`` None is an explicit command-line import; it retains the existing owner and history.
    """
    now = now_ms()
    with engine.begin() as conn:
        existing = conn.execute(select(course).where(course.c.uid == uid).with_for_update()).first()
        if existing is None:
            gone = conn.execute(select(course_gone).where(course_gone.c.uid == uid)).first()
            if gone is not None:
                if owner_id is not None and gone.owner_id not in (None, owner_id):
                    raise NotYours(uid)
                raise SyncConflict("course_deleted", gone.revision)
            if expected not in (None, 0):
                raise SyncConflict("revision_conflict", 0)
            share_id = new_share_id()
            while conn.execute(select(course.c.id).where(course.c.share_id == share_id)).first():
                share_id = new_share_id()
            conn.execute(course.insert().values(uid=uid, share_id=share_id, owner_id=owner_id, created_at=now,
                                                updated_at=now, **values))
            _snapshot(conn, conn.execute(select(course).where(course.c.uid == uid)).one(), "created", device)
            return True, True
        if owner_id is not None and existing.owner_id not in (None, owner_id):
            raise NotYours(uid)
        if existing.deleted_at is not None:
            raise SyncConflict("course_deleted", existing.revision)
        changed = existing.content_hash != values["content_hash"]
        if owner_id is not None and (changed or existing.visibility != values["visibility"]):
            _expected(existing, expected)
            if changed:
                from .changes import summarize
                from .payload import confirmation_hash
                impact = summarize(json.loads(existing.content), json.loads(values["content"]), notes_limit, items_limit)
                approval = confirmation_hash(values, json.loads(values["content"]))
                if impact["large"] and confirmation != approval:
                    raise SyncConflict("confirmation_required", existing.revision, changes=impact,
                                       confirmation=approval)
        update: dict = {}
        if changed:
            update = {**values, "updated_at": now}
        elif existing.visibility != values["visibility"]:
            update["visibility"] = values["visibility"]
        if owner_id is not None and existing.owner_id is None:
            update["owner_id"] = owner_id
        if update:
            _update(conn, existing, update, "updated" if changed else "visibility", device)
        return False, changed


def delete_course(engine: Engine, course_id: int, uid: str, owner_id: int | None, reason: str, *,
                  expected: int | None = None, confirmation: str | None = None, device: str = "") -> None:
    """Hides the course, retains content/reports, and emits a revision-bound deletion for safe devices."""
    with engine.begin() as conn:
        row = conn.execute(select(course).where(course.c.id == course_id).with_for_update()).one()
        if row.owner_id != owner_id:
            raise NotYours(uid)
        if row.deleted_at is not None and expected == row.revision - 1 and confirmation == f"delete-{expected}-{reason}":
            return  # A retry after a lost response; a later restore invalidates this approval.
        _expected(row, expected)
        if confirmation != f"delete-{row.revision}-{reason}":
            raise SyncConflict("confirmation_required", row.revision,
                               confirmation=f"delete-{row.revision}-{reason}")
        if row.deleted_at is not None:
            return
        _update(conn, row, {"deleted_at": now_ms()}, reason, device)
        conn.execute(course_gone.delete().where(course_gone.c.uid == uid))
        conn.execute(course_gone.insert().values(uid=uid, owner_id=owner_id, reason=reason,
                                               deleted_at=now_ms(), revision=row.revision + 1))


def restore_course(engine: Engine, uid: str, owner_id: int, revision: int, expected: int,
                   confirmation: str, device: str = "", visibility: str | None = None) -> None:
    with engine.begin() as conn:
        row = conn.execute(select(course).where(course.c.uid == uid).with_for_update()).first()
        if row is None or row.owner_id != owner_id:
            raise NotYours(uid)
        _expected(row, expected)
        approval = f"restore-{expected}-{revision}" + (f"-{visibility}" if visibility is not None else "")
        if confirmation != approval:
            raise SyncConflict("confirmation_required", row.revision, confirmation=approval)
        snapshot = conn.execute(select(course_revision.c.snapshot)
                                .where(course_revision.c.course_id == row.id,
                                       course_revision.c.revision == revision)).scalar_one_or_none()
        if snapshot is None:
            raise SyncConflict("revision_not_found", row.revision)
        saved = json.loads(snapshot)
        values = {k: saved[k] for k in ("title", "certification", "lang", "position", "visibility", "content",
                                      "content_hash", "question_count", "card_count")}
        values.update(deleted_at=None, updated_at=now_ms(), modified_at=now_ms())
        if visibility is not None:
            values["visibility"] = visibility
        _update(conn, row, values, "restored", device)
        conn.execute(course_gone.delete().where(course_gone.c.uid == uid))


def prune(engine: Engine, history_limit: int = 20, trash_days: int = 30) -> None:
    """Bound history; retain tombstones after expired trash is purged to stop stale resurrection."""
    with engine.begin() as conn:
        for row in conn.execute(select(course.c.id, course.c.revision, course.c.deleted_at)).all():
            if row.deleted_at is not None and row.deleted_at < now_ms() - trash_days * 86_400_000:
                conn.execute(report.delete().where(report.c.course_id == row.id))
                conn.execute(course_revision.delete().where(course_revision.c.course_id == row.id))
                conn.execute(course.delete().where(course.c.id == row.id))
            elif row.deleted_at is None:
                conn.execute(course_revision.delete().where(course_revision.c.course_id == row.id,
                                                            course_revision.c.revision <= row.revision - history_limit))


def give_courses(engine: Engine, token_id: int, uids: list[str] | None = None, from_id: int | None = None,
                 unowned: bool = False) -> int:
    """Makes ``token_id`` the owner of the given courses, of every course of token ``from_id``, or of every
    course without an owner. Returns how many changed hands."""
    conditions = []
    if uids:
        conditions.append(course.c.uid.in_(uids))
    if from_id is not None:
        conditions.append(course.c.owner_id == from_id)
    if unowned:
        conditions.append(course.c.owner_id.is_(None))
    if not conditions:
        return 0
    with engine.begin() as conn:
        done = conn.execute(course.update().where(or_(*conditions)).values(owner_id=token_id)).rowcount
        if from_id is not None:
            conn.execute(course_gone.update().where(course_gone.c.owner_id == from_id).values(owner_id=token_id))
        return done


# --- Share ids ----------------------------------------------------------------------------------

_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_share_id(length: int = 10) -> str:
    """About 58 bits: unguessable, short enough to read aloud, no look-alike characters."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))
