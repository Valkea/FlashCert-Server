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
    UniqueConstraint,
    and_,
    create_engine,
    event,
    func,
    inspect,
    or_,
    select,
    text,
)
from sqlalchemy.engine import Engine

SCHEMA_VERSION = 4

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
    #: "user": made by the admin, backs up courses of its own. "guest": made when someone joined through an invite or
    #: a shared link; it only reaches the courses it was given and can't send new ones (`token promote` lifts that).
    Column("kind", String(10), nullable=False, default="user"),
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
    #: What the owner lets people with the link do from the app (copy, follow, join), comma separated, within what
    #: the server allows. None: everything the server allows.
    Column("link_actions", String(40)),
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
    #: The token that made the revision: the owner or a member allowed to edit.
    Column("token_id", Integer),
)

#: A certification shared as a whole: every course of its owner with that certification name, now and later.
collection = Table(
    "collection",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("owner_id", Integer, nullable=False),
    Column("name", String(200), nullable=False),
    Column("share_id", String(16), nullable=False, unique=True),
    #: "private": members only, no page. "unlisted" or "public": a page listing its shared courses.
    Column("visibility", String(10), nullable=False, default="private"),
    Column("link_actions", String(40)),
    Column("created_at", BigInteger, nullable=False),
    UniqueConstraint("owner_id", "name"),
)

#: Another token's access to a course or to a whole certification. via "invite": the owner chose this person, private
#: courses included. via "link": taken from a shared page, so it covers what is still shared (not private) only.
membership = Table(
    "membership",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("token_id", Integer, nullable=False),
    Column("course_id", Integer, ForeignKey("course.id", ondelete="CASCADE")),
    Column("collection_id", Integer, ForeignKey("collection.id", ondelete="CASCADE")),
    #: viewer | editor
    Column("role", String(10), nullable=False),
    Column("via", String(10), nullable=False),
    Column("created_at", BigInteger, nullable=False),
)

#: A link the owner hands to someone: redeeming it gives access to a course or a certification. Only a SHA-256 of
#: the code is kept, like tokens.
invite = Table(
    "invite",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("hash", String(64), nullable=False, unique=True),
    Column("course_id", Integer, ForeignKey("course.id", ondelete="CASCADE")),
    Column("collection_id", Integer, ForeignKey("collection.id", ondelete="CASCADE")),
    Column("role", String(10), nullable=False),
    Column("created_by", Integer, nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("expires_at", BigInteger),
    Column("uses_left", Integer, nullable=False),
    Column("revoked_at", BigInteger),
)

#: Courses of other tokens a member's devices were given, so they learn when that access ends and keep their copies.
access_seen = Table(
    "access_seen",
    metadata,
    Column("token_id", Integer, primary_key=True),
    Column("uid", String(100), primary_key=True),
    Column("revision", BigInteger, nullable=False),
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
Index("membership_token", membership.c.token_id)
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
        if version < 4:
            _to_v4(conn)  # first: the older steps read whole rows, new columns included
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
    _add_columns(conn, {"course": {"revision": "BIGINT NOT NULL DEFAULT 1", "deleted_at": "BIGINT",
                                   "event": "VARCHAR(16) NOT NULL DEFAULT 'created'"},
                        "course_gone": {"revision": "BIGINT NOT NULL DEFAULT 0"}})
    for row in conn.execute(select(course)).all():
        _snapshot(conn, row, "migrated", "")


def _to_v4(conn) -> None:
    """Guests, link actions and revision authors; memberships, invites and collections are new tables."""
    _add_columns(conn, {"token": {"kind": "VARCHAR(10) NOT NULL DEFAULT 'user'"},
                        "course": {"link_actions": "VARCHAR(40)"},
                        "course_revision": {"token_id": "INTEGER"}})


def _add_columns(conn, tables: dict[str, dict[str, str]]) -> None:
    for table, columns in tables.items():
        present = {c["name"] for c in inspect(conn).get_columns(table)}
        for name, definition in columns.items():
            if name not in present:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


# --- Tokens -------------------------------------------------------------------------------------


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_token(engine: Engine, label: str = "", kind: str = "user") -> tuple[int, str]:
    with engine.begin() as conn:
        return _new_token(conn, label, kind)


def _new_token(conn, label: str, kind: str) -> tuple[int, str]:
    raw = "fcs_" + secrets.token_urlsafe(32)
    result = conn.execute(token.insert().values(label=label[:100], hash=hash_token(raw), created_at=now_ms(), kind=kind))
    return result.inserted_primary_key[0], raw


def token_info(engine: Engine, token_id: int):
    with engine.connect() as conn:
        return conn.execute(select(token.c.id, token.c.label, token.c.kind).where(token.c.id == token_id)).first()


def promote_token(engine: Engine, token_id: int) -> bool:
    """A guest becomes a user: it may then back up courses of its own."""
    with engine.begin() as conn:
        return conn.execute(token.update().where(token.c.id == token_id, token.c.kind == "guest")
                            .values(kind="user")).rowcount > 0


def rename_token(engine: Engine, token_id: int, label: str) -> bool:
    """The name other people see for this token: as the owner of what it shares, as a member, as an author."""
    with engine.begin() as conn:
        return conn.execute(token.update().where(token.c.id == token_id).values(label=label[:100])).rowcount > 0


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
        rows = conn.execute(select(token.c.id, token.c.label, token.c.kind, token.c.created_at, token.c.last_used_at,
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


def _snapshot(conn, row, action: str, device: str, author: int | None = None) -> None:
    conn.execute(course_revision.insert().values(course_id=row.id, revision=row.revision,
                 snapshot=json.dumps(dict(row._mapping), ensure_ascii=False), action=action,
                 device=device[:100], created_at=now_ms(),
                 token_id=author if author is not None else row.owner_id))


def _expected(row, expected: int | None) -> None:
    if expected is None:
        raise SyncConflict("revision_required", row.revision)
    if expected != row.revision:
        raise SyncConflict("revision_conflict", row.revision)


def _update(conn, row, values: dict, action: str, device: str, author: int | None = None) -> None:
    owner = course.c.owner_id.is_(None) if row.owner_id is None else course.c.owner_id == row.owner_id
    done = conn.execute(course.update().where(course.c.id == row.id, course.c.revision == row.revision, owner)
                        .values(**values, revision=row.revision + 1, event=action)).rowcount
    if done != 1:
        raise SyncConflict("revision_conflict", row.revision)
    _snapshot(conn, conn.execute(select(course).where(course.c.id == row.id)).one(), action, device, author)


def save_course(engine: Engine, uid: str, values: dict, owner_id: int | None, *, expected: int | None = None,
                confirmation: str | None = None, device: str = "", notes_limit: int = 30,
                items_limit: int = 20, author: int | None = None) -> tuple[bool, bool]:
    """Creates or replaces course ``uid``; returns (created, changed).

    ``values`` are the columns taken from the document: the meta, content, content_hash, counts and modified_at.
    A token can only send its own courses and claims one without an owner. Deleted courses require explicit
    restore. ``owner_id`` None is an explicit command-line import; it retains the existing owner and history.
    A member allowed to edit passes the owner's id and its own as ``author``, which the revision records.
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
            _update(conn, existing, update, "updated" if changed else "visibility", device, author)
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
                   confirmation: str, device: str = "", visibility: str | None = None, author: int | None = None) -> None:
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
        _update(conn, row, values, "restored", device, author)
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
            taken = set(conn.execute(select(collection.c.name).where(collection.c.owner_id == token_id)).scalars())
            moved = collection.update().where(collection.c.owner_id == from_id)
            if taken:
                moved = moved.where(collection.c.name.not_in(taken))
            conn.execute(moved.values(owner_id=token_id))
            conn.execute(invite.update().where(invite.c.created_by == from_id).values(created_by=token_id))
        return done


# --- Sharing with other tokens ------------------------------------------------------------------

ROLES = ("viewer", "editor", "owner")


def rank(role: str | None) -> int:
    return ROLES.index(role) if role in ROLES else -1


def shared_with(conn, token_id: int) -> dict[int, str]:
    """Live courses of other tokens that ``token_id`` was given, with its highest role on each: course id → role.

    A membership taken from a shared link only covers what is still shared: courses that aren't private, and for a
    certification, only while the certification itself is shared.
    """
    roles: dict[int, str] = {}
    for m in conn.execute(select(membership).where(membership.c.token_id == token_id)).all():
        query = select(course.c.id).where(course.c.deleted_at.is_(None), course.c.owner_id != token_id)
        if m.course_id is not None:
            query = query.where(course.c.id == m.course_id)
        else:
            col = conn.execute(select(collection).where(collection.c.id == m.collection_id)).first()
            if col is None or (m.via == "link" and col.visibility == "private"):
                continue
            query = query.where(course.c.owner_id == col.owner_id, course.c.certification == col.name)
        if m.via == "link":
            query = query.where(course.c.visibility != "private")
        for course_id in conn.execute(query).scalars():
            if rank(m.role) > rank(roles.get(course_id)):
                roles[course_id] = m.role
    return roles


def role_of(conn, token_id: int, row) -> str | None:
    """"owner", "editor", "viewer" or None: what ``token_id`` may do with the course ``row``."""
    if row.owner_id is not None and row.owner_id == token_id:
        return "owner"
    if row.deleted_at is not None:
        return None
    return shared_with(conn, token_id).get(row.id)


def member_courses(engine: Engine, token_id: int) -> tuple[list, dict[int, str], list]:
    """Other tokens' courses this token works on: (rows, roles, ended), where ``ended`` lists the access_seen rows of
    courses its devices were given before and no longer reach (removed member, course deleted or made private…)."""
    with engine.begin() as conn:
        roles = shared_with(conn, token_id)
        rows = conn.execute(select(course).where(course.c.id.in_(roles)).order_by(course.c.id)).all() if roles else []
        seen = {r.uid: r for r in conn.execute(select(access_seen).where(access_seen.c.token_id == token_id)).all()}
        for row in rows:
            if row.uid in seen:
                if seen.pop(row.uid).revision != row.revision:
                    conn.execute(access_seen.update().where(access_seen.c.token_id == token_id,
                                                            access_seen.c.uid == row.uid).values(revision=row.revision))
            else:
                conn.execute(access_seen.insert().values(token_id=token_id, uid=row.uid, revision=row.revision))
        owned = set(conn.execute(select(course.c.uid).where(course.c.owner_id == token_id,
                                                            course.c.uid.in_(list(seen)))).scalars()) if seen else set()
        ended = [r for uid, r in seen.items() if uid not in owned]
    return rows, roles, ended


def grant(conn, token_id: int, role: str, via: str, course_id: int | None = None,
          collection_id: int | None = None) -> int:
    """Gives ``token_id`` a role on a course or a certification; an existing membership only ever goes up."""
    target = (membership.c.course_id == course_id) if course_id is not None else (membership.c.collection_id == collection_id)
    existing = conn.execute(select(membership).where(membership.c.token_id == token_id, target)).first()
    if existing is None:
        return conn.execute(membership.insert().values(token_id=token_id, course_id=course_id, collection_id=collection_id,
                                                       role=role, via=via, created_at=now_ms())).inserted_primary_key[0]
    values = {}
    if rank(role) > rank(existing.role):
        values["role"] = role
    if via == "invite" and existing.via != "invite":
        values["via"] = "invite"
    if values:
        conn.execute(membership.update().where(membership.c.id == existing.id).values(**values))
    return existing.id


def members(engine: Engine, owner_id: int, course_id: int | None = None, collection_id: int | None = None) -> list[dict]:
    target = (membership.c.course_id == course_id) if course_id is not None else (membership.c.collection_id == collection_id)
    with engine.connect() as conn:
        rows = conn.execute(select(membership, token.c.label, token.c.kind, token.c.last_used_at)
                            .join(token, token.c.id == membership.c.token_id).where(target)
                            .order_by(membership.c.id)).all()
    return [{"id": r.id, "token_id": r.token_id, "label": r.label, "guest": r.kind == "guest", "role": r.role,
             "via": r.via, "created_at": r.created_at, "last_used_at": r.last_used_at} for r in rows]


def membership_owner(conn, membership_id: int):
    """(membership row, owner id of what it gives access to), or None."""
    m = conn.execute(select(membership).where(membership.c.id == membership_id)).first()
    if m is None:
        return None
    if m.course_id is not None:
        owner = conn.execute(select(course.c.owner_id).where(course.c.id == m.course_id)).scalar_one_or_none()
    else:
        owner = conn.execute(select(collection.c.owner_id).where(collection.c.id == m.collection_id)).scalar_one_or_none()
    return m, owner


# --- Certifications shared as a whole ------------------------------------------------------------


def collection_for(conn, owner_id: int, name: str, create: bool = False):
    row = conn.execute(select(collection).where(collection.c.owner_id == owner_id, collection.c.name == name)).first()
    if row is None and create:
        share_id = new_share_id()
        while conn.execute(select(collection.c.id).where(collection.c.share_id == share_id)).first() or \
                conn.execute(select(course.c.id).where(course.c.share_id == share_id)).first():
            share_id = new_share_id()
        conn.execute(collection.insert().values(owner_id=owner_id, name=name[:200], share_id=share_id,
                                                visibility="private", created_at=now_ms()))
        row = conn.execute(select(collection).where(collection.c.owner_id == owner_id, collection.c.name == name)).first()
    return row


def collection_courses(conn, col, include_private: bool) -> list:
    """The certification's live courses, in the owner's order."""
    query = select(course).where(course.c.owner_id == col.owner_id, course.c.certification == col.name,
                                 course.c.deleted_at.is_(None))
    if not include_private:
        query = query.where(course.c.visibility != "private")
    rows = conn.execute(query).all()
    rows.sort(key=lambda r: (r.position is None, r.position or 0, r.title.casefold()))
    return rows


# --- Invites -------------------------------------------------------------------------------------


def create_invite(engine: Engine, owner_id: int, role: str, uses: int, expires_at: int | None,
                  course_id: int | None = None, collection_id: int | None = None) -> tuple[int, str]:
    """Returns (id, code). The code is shown once; only its hash is kept."""
    code = secrets.token_urlsafe(18)
    with engine.begin() as conn:
        result = conn.execute(invite.insert().values(hash=hash_token(code), course_id=course_id,
                                                     collection_id=collection_id, role=role, created_by=owner_id,
                                                     created_at=now_ms(), expires_at=expires_at, uses_left=uses))
        return result.inserted_primary_key[0], code


def _valid_invite():
    now = now_ms()
    return and_(invite.c.revoked_at.is_(None), invite.c.uses_left > 0,
                or_(invite.c.expires_at.is_(None), invite.c.expires_at > now))


def find_invite(conn, code: str):
    """The invite behind a code while it can still be used, or None."""
    if not code or len(code) > 100:
        return None
    return conn.execute(select(invite).where(invite.c.hash == hash_token(code), _valid_invite())).first()


class InviteInvalid(Exception):
    """Unknown, revoked, used up or expired."""


def join(engine: Engine, token_id: int | None, label: str, *, code: str | None = None, course_id: int | None = None,
         collection_id: int | None = None, role: str = "viewer") -> tuple[int, str | None, int | None]:
    """Gives access through an invite code, or with ``role`` through a shared link (``course_id``/``collection_id``,
    checked by the caller, as the role is). Without ``token_id`` a guest token is made; with one, a ``label`` renames
    it. Returns (token id, new raw token or None, membership id; None when the token already owns what it joined)."""
    with engine.begin() as conn:
        via = "link"
        if code is not None:
            inv = conn.execute(select(invite).where(invite.c.hash == hash_token(code), _valid_invite())
                               .with_for_update()).first()
            if inv is None:
                raise InviteInvalid(code)
            role, via, course_id, collection_id = inv.role, "invite", inv.course_id, inv.collection_id
        if course_id is not None:
            row = conn.execute(select(course.c.owner_id, course.c.deleted_at).where(course.c.id == course_id)).one()
            if row.deleted_at is not None:
                raise InviteInvalid(code or "")
            owner = row.owner_id
        else:
            owner = conn.execute(select(collection.c.owner_id).where(collection.c.id == collection_id)).scalar_one()
        if token_id is not None and token_id == owner:
            return token_id, None, None
        if code is not None:
            used = conn.execute(invite.update().where(invite.c.id == inv.id, invite.c.uses_left > 0)
                                .values(uses_left=invite.c.uses_left - 1)).rowcount
            if used != 1:
                raise InviteInvalid(code)
        raw = None
        if token_id is None:
            token_id, raw = _new_token(conn, label or "guest", "guest")
        elif label:
            conn.execute(token.update().where(token.c.id == token_id).values(label=label[:100]))
        membership_id = grant(conn, token_id, role, via, course_id, collection_id)
        return token_id, raw, membership_id


# --- Share ids ----------------------------------------------------------------------------------

_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_share_id(length: int = 10) -> str:
    """About 58 bits: unguessable, short enough to read aloud, no look-alike characters."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))
