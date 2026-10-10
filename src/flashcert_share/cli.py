"""Command line: run the server and manage tokens, courses and reports.

    flashcert-share serve [--host 127.0.0.1] [--port 8080]
    flashcert-share token create [--label phone]     # prints the token once
    flashcert-share token list | token revoke ID
    flashcert-share courses                          # what is shared, with links
    flashcert-share import bundle.json [--uid X]     # share a course file without the app
    flashcert-share reports [--all]                  # errors flagged by visitors
    flashcert-share copy-db postgresql+psycopg://…   # move everything to another database
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime

from sqlalchemy import select

from . import __version__, db
from .config import Config


def _when(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M") if ms else "never"


def _engine(cfg: Config):
    engine = db.make_engine(cfg.database_url)
    db.init(engine)
    return engine


def make_server(cfg: Config, host: str, port: int, threads: int = 4):
    """The waitress server for the app. X-Forwarded-* headers are left to the app (ProxyFix), which trusts only
    the last FLASHCERT_SHARE_PROXIES hops; waitress would otherwise drop them before the app sees them."""
    from waitress import create_server

    from .app import create_app

    app = create_app(cfg)
    server = create_server(app, host=host, port=port, threads=threads, ident="flashcert-share",
                           clear_untrusted_proxy_headers=cfg.proxies == 0)
    return app, server


def cmd_serve(cfg: Config, args) -> None:
    app, server = make_server(cfg, args.host, args.port, args.threads)
    engine = app.extensions["flashcert"]["engine"]
    print(f"flashcert-share {__version__} on http://{args.host}:{args.port}  (database: {engine.url!r})")
    if db.token_count(engine) == 0:
        print("No sync token yet: run `flashcert-share token create` and enter it in the app.")
    server.run()


def cmd_token(cfg: Config, args) -> None:
    engine = _engine(cfg)
    if args.action == "create":
        token_id, raw = db.create_token(engine, args.label or "")
        print(f"Token #{token_id} created. Enter it in the app (Settings → Sharing); it won't be shown again:\n")
        print(raw)
    elif args.action == "list":
        rows = db.list_tokens(engine)
        if not rows:
            print("No tokens.")
        for r in rows:
            print(f"#{r['id']:<4} {r['label'] or '-':<20} created {_when(r['created_at'])}  last used {_when(r['last_used_at'])}")
    elif args.action == "revoke":
        if args.id is None:
            sys.exit("token revoke needs a token id (see `token list`)")
        print("Revoked." if db.revoke_token(engine, args.id) else "No such token.")


def _link(cfg: Config, share_id: str) -> str:
    return f"{cfg.public_url}/c/{share_id}" if cfg.public_url else f"/c/{share_id}"


def cmd_courses(cfg: Config, args) -> None:
    engine = _engine(cfg)
    with engine.connect() as conn:
        rows = conn.execute(select(db.course).order_by(db.course.c.certification, db.course.c.title)).all()
    if not rows:
        print("No course shared yet.")
    for r in rows:
        print(f"{r.visibility:<9} {_link(cfg, r.share_id):<40} {r.title}  [{r.certification or '-'}]  "
              f"{r.question_count} q, {r.card_count} cards, updated {_when(r.updated_at)}  (uid {r.uid})")


def cmd_import(cfg: Config, args) -> None:
    """Shares a course file directly, as the app would: useful to try the server before the app syncs."""
    from .app import create_app

    with open(args.file, encoding="utf-8") as f:
        doc = json.load(f)
    if args.unlisted:
        doc["visibility"] = "unlisted"
    if args.certification:
        doc["certification"] = args.certification
    uid = args.uid or "import-" + hashlib.sha1((doc.get("title") or args.file).encode("utf-8")).hexdigest()[:12]
    app = create_app(cfg)
    engine = app.extensions["flashcert"]["engine"]
    token_id, raw = db.create_token(engine, "import (temporary)")
    try:
        client = app.test_client()
        r = client.put(f"/api/v1/courses/{uid}", json=doc, headers={"Authorization": f"Bearer {raw}"},
                       base_url=cfg.public_url or "http://localhost")
    finally:
        db.revoke_token(engine, token_id)
    body = r.get_json()
    if r.status_code >= 400:
        sys.exit(f"Import failed: {body.get('error')}")
    for w in body.get("warnings", []):
        print("warning:", w)
    state = "created" if body["created"] else ("updated" if body["changed"] else "unchanged")
    link = body["url"] if cfg.public_url else f"/c/{body['share_id']} (set FLASHCERT_SHARE_PUBLIC_URL for full links)"
    print(f"{state}: {body['title']}  ({body['questions']} questions, {body['cards']} cards)\n{link}")


def cmd_reports(cfg: Config, args) -> None:
    engine = _engine(cfg)
    stmt = (select(db.report, db.course.c.title).join(db.course, db.course.c.id == db.report.c.course_id)
            .order_by(db.report.c.id))
    if not args.all:
        stmt = stmt.where(db.report.c.status == "open")
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    if not rows:
        print("No open reports." if not args.all else "No reports.")
    for r in rows:
        who = f" — {r.author}" if r.author else ""
        print(f"#{r.id} [{r.status}] {_when(r.created_at)}  {r.title} › {r.element_kind} {r.element_id}".rstrip())
        print(f"    “{r.excerpt[:140]}”")
        print(f"    {r.message}{who}\n")
    if args.resolve:
        with engine.begin() as conn:
            conn.execute(db.report.update().where(db.report.c.id.in_(args.resolve))
                         .values(status="resolved", updated_at=db.now_ms()))
        print(f"Marked resolved: {', '.join(map(str, args.resolve))}")


def cmd_copy_db(cfg: Config, args) -> None:
    """Copies every table to another database (for instance SQLite → PostgreSQL). The target must be empty."""
    source = _engine(cfg)
    target = db.make_engine(args.target_url)
    db.metadata.create_all(target)
    with target.connect() as conn:
        if conn.execute(select(db.course.c.id).limit(1)).first() or conn.execute(select(db.token.c.id).limit(1)).first():
            sys.exit("The target database already has data; refusing to mix the two.")
    with source.connect() as src, target.begin() as dst:
        dst.execute(db.meta.delete())
        for table in db.metadata.sorted_tables:
            rows = [dict(r._mapping) for r in src.execute(select(table))]
            if rows:
                dst.execute(table.insert(), rows)
            print(f"{table.name}: {len(rows)} rows")
        if target.dialect.name == "postgresql":
            from sqlalchemy import text

            for table in ("token", "course", "report"):
                dst.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                                 f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"))
    print("Done. Point FLASHCERT_SHARE_DATABASE_URL at the new database and restart the server.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="flashcert-share", description="Read-only web sharing for FlashCert courses.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--database-url", help="SQLAlchemy URL (default: FLASHCERT_SHARE_DATABASE_URL or a SQLite file)")
    parser.add_argument("--public-url", help="base of share links, e.g. https://courses.example.com")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the web server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--proxies", type=int, help="reverse proxies in front (default 1; 0 when exposed directly)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("token", help="manage the app's sync tokens")
    p.add_argument("action", choices=("create", "list", "revoke"))
    p.add_argument("id", nargs="?", type=int)
    p.add_argument("--label", help="a name to recognise the token, e.g. 'pixel'")
    p.set_defaults(func=cmd_token)

    p = sub.add_parser("courses", help="list shared courses")
    p.set_defaults(func=cmd_courses)

    p = sub.add_parser("import", help="share a course JSON file (flashcert-share-1 or flashcert-course-1)")
    p.add_argument("file")
    p.add_argument("--uid", help="course id; importing again with the same uid replaces it")
    p.add_argument("--certification")
    p.add_argument("--unlisted", action="store_true", help="reachable by link only, not on the list")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("reports", help="errors flagged by visitors")
    p.add_argument("--all", action="store_true", help="include resolved and dismissed reports")
    p.add_argument("--resolve", type=int, nargs="+", metavar="ID", help="mark these reports resolved")
    p.set_defaults(func=cmd_reports)

    p = sub.add_parser("copy-db", help="copy all data to another (empty) database")
    p.add_argument("target_url")
    p.set_defaults(func=cmd_copy_db)

    args = parser.parse_args(argv)
    cfg = Config.from_env(database_url=args.database_url, public_url=(args.public_url or "").rstrip("/") or None,
                          proxies=getattr(args, "proxies", None))
    args.func(cfg, args)


if __name__ == "__main__":
    main()
