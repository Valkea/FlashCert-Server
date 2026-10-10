"""Storage. SQLAlchemy Core keeps the SQL portable: SQLite by default, PostgreSQL by changing the URL."""

from __future__ import annotations

import hashlib
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
    select,
)
from sqlalchemy.engine import Engine

SCHEMA_VERSION = 1

metadata = MetaData()

meta = Table(
    "meta",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
)

#: Sync credentials. Only a SHA-256 of each token is kept; the token itself is shown once.
token = Table(
    "token",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("label", String(100), nullable=False, default=""),
    Column("hash", String(64), nullable=False, unique=True),
    Column("created_at", BigInteger, nullable=False),
    Column("last_used_at", BigInteger),
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
    #: "public": on the course list. "unlisted": reachable only through its link.
    Column("visibility", String(10), nullable=False, default="public"),
    #: The normalised course document (notes, sheet, questions, cards) as JSON.
    Column("content", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("question_count", Integer, nullable=False, default=0),
    Column("card_count", Integer, nullable=False, default=0),
    Column("created_at", BigInteger, nullable=False),
    #: Last time the content changed (a re-sync of identical content leaves it alone).
    Column("updated_at", BigInteger, nullable=False),
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
    """Creates missing tables. Later schema changes go through SCHEMA_VERSION steps here."""
    metadata.create_all(engine)
    with engine.begin() as conn:
        row = conn.execute(select(meta.c.value).where(meta.c.key == "schema_version")).first()
        if row is None:
            conn.execute(meta.insert().values(key="schema_version", value=str(SCHEMA_VERSION)))


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


def check_token(engine: Engine, raw: str) -> bool:
    if not raw:
        return False
    digest = hash_token(raw)
    with engine.begin() as conn:
        row = conn.execute(select(token.c.id, token.c.last_used_at).where(token.c.hash == digest)).first()
        if row is None:
            return False
        # One write a minute at most: the app syncs in bursts.
        if row.last_used_at is None or now_ms() - row.last_used_at > 60_000:
            conn.execute(token.update().where(token.c.id == row.id).values(last_used_at=now_ms()))
        return True


def list_tokens(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(token.c.id, token.c.label, token.c.created_at, token.c.last_used_at)
                            .order_by(token.c.id))
        return [dict(r._mapping) for r in rows]


def revoke_token(engine: Engine, token_id: int) -> bool:
    with engine.begin() as conn:
        return conn.execute(token.delete().where(token.c.id == token_id)).rowcount > 0


def token_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(token)).scalar_one()


# --- Share ids ----------------------------------------------------------------------------------

_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_share_id(length: int = 10) -> str:
    """About 58 bits: unguessable, short enough to read aloud, no look-alike characters."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))
