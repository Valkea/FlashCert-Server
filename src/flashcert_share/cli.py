"""Command line: run the server and manage tokens, courses and reports.

    flashcert-share serve [--host 127.0.0.1] [--port 8080]
    flashcert-share token create [--label phone]     # prints the token once
    flashcert-share token list | token revoke ID | token promote ID   # promote: a guest may back up courses
    flashcert-share courses                          # what is shared, with links and owners
    flashcert-share owner TOKEN_ID UID… | --from ID | --unowned   # who may change and download courses
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

from sqlalchemy import func, select

from . import __version__, db, payload
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


def _unowned(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(db.course).where(db.course.c.owner_id.is_(None))).scalar_one()


def cmd_serve(cfg: Config, args) -> None:
    app, server = make_server(cfg, args.host, args.port, args.threads)
    engine = app.extensions["flashcert"]["engine"]
    print(f"flashcert-share {__version__} on http://{args.host}:{args.port}  (database: {engine.url!r})")
    if db.token_count(engine) == 0:
        print("No sync token yet: run `flashcert-share token create` and enter it in the app.")
    unowned = _unowned(engine)
    if unowned:
        print(f"{unowned} course(s) have no owner yet: the next app that sends one claims it, or run "
              "`flashcert-share owner TOKEN_ID UID…` (see `courses` and `token list`).")
    server.run()


def cmd_token(cfg: Config, args) -> None:
    engine = _engine(cfg)
    if args.action == "create":
        token_id, raw = db.create_token(engine, args.label or "")
        print(f"Token #{token_id} created. Enter it in the app (Settings → Share and sync); it won't be shown again:\n")
        print(raw)
    elif args.action == "list":
        rows = db.list_tokens(engine)
        if not rows:
            print("No tokens.")
        for r in rows:
            state = f"REVOKED {_when(r['revoked_at'])}" if r["revoked_at"] else f"last used {_when(r['last_used_at'])}"
            kind = " (guest)" if r["kind"] == "guest" else ""
            print(f"#{r['id']:<4} {(r['label'] or '-') + kind:<28} created {_when(r['created_at'])}  {state}  "
                  f"{r['courses']} course(s)")
    elif args.action == "revoke":
        if args.id is None:
            sys.exit("token revoke needs a token id (see `token list`)")
        if not db.revoke_token(engine, args.id):
            sys.exit("No such token, or already revoked.")
        owned = next((r["courses"] for r in db.list_tokens(engine) if r["id"] == args.id), 0)
        print("Revoked.")
        if owned:
            print(f"Its {owned} course(s) stay online. To hand them to a new token: "
                  f"flashcert-share owner NEW_ID --from {args.id}")
    elif args.action == "promote":
        if args.id is None:
            sys.exit("token promote needs a token id (see `token list`)")
        if not db.promote_token(engine, args.id):
            sys.exit("No such guest token.")
        print("Promoted: this token may now back up courses of its own.")


def _link(cfg: Config, share_id: str) -> str:
    return f"{cfg.public_url}/c/{share_id}" if cfg.public_url else f"/c/{share_id}"


def cmd_courses(cfg: Config, args) -> None:
    engine = _engine(cfg)
    labels = {t["id"]: t["label"] or "-" for t in db.list_tokens(engine)}
    with engine.connect() as conn:
        rows = conn.execute(select(db.course).order_by(db.course.c.certification, db.course.c.title)).all()
    if not rows:
        print("No course shared yet.")
    for r in rows:
        owner = f"#{r.owner_id} {labels.get(r.owner_id, '?')}" if r.owner_id is not None else "no owner"
        print(f"{r.visibility:<9} {_link(cfg, r.share_id):<40} {r.title}  [{r.certification or '-'}]  "
              f"{r.question_count} q, {r.card_count} cards, updated {_when(r.updated_at)}  ({owner}, uid {r.uid})")


def cmd_owner(cfg: Config, args) -> None:
    """Hands courses to a token: it alone can then change, download and delete them from the app."""
    engine = _engine(cfg)
    if not args.uids and args.from_id is None and not args.unowned:
        sys.exit("Name the courses: UID…, --from TOKEN_ID or --unowned.")
    live = {t["id"] for t in db.list_tokens(engine) if not t["revoked_at"]}
    if args.token_id not in live:
        sys.exit(f"No token #{args.token_id} in use (see `token list`).")
    n = db.give_courses(engine, args.token_id, args.uids, args.from_id, args.unowned)
    print(f"{n} course(s) now belong to token #{args.token_id}.")


def cmd_import(cfg: Config, args) -> None:
    """Shares a course file directly, as the app would: useful to try the server before the app syncs."""
    with open(args.file, encoding="utf-8") as f:
        doc = json.load(f)
    if args.unlisted:
        doc["visibility"] = "unlisted"
    if args.certification:
        doc["certification"] = args.certification
    uid = args.uid or "import-" + hashlib.sha1((doc.get("title") or args.file).encode("utf-8")).hexdigest()[:12]
    try:
        meta, content, warnings = payload.normalize(doc)
    except payload.PayloadError as e:
        sys.exit(f"Import failed: {e}")
    engine = _engine(cfg)
    if args.owner is not None and args.owner not in {t["id"] for t in db.list_tokens(engine) if not t["revoked_at"]}:
        sys.exit(f"No token #{args.owner} in use (see `token list`).")
    values = {**meta, "modified_at": meta["modified_at"] or db.now_ms(),
              "content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
              "content_hash": payload.content_hash(meta, content),
              "question_count": len(content["questions"]), "card_count": len(content["cards"])}
    created, changed = db.save_course(engine, uid, values, None)
    if args.owner is not None:
        db.give_courses(engine, args.owner, [uid])
    for w in warnings:
        print("warning:", w)
    with engine.connect() as conn:
        share_id = conn.execute(select(db.course.c.share_id).where(db.course.c.uid == uid)).scalar_one()
    state = "created" if created else ("updated" if changed else "unchanged")
    link = _link(cfg, share_id) if cfg.public_url else f"/c/{share_id} (set FLASHCERT_SHARE_PUBLIC_URL for full links)"
    print(f"{state}: {meta['title']}  ({len(content['questions'])} questions, {len(content['cards'])} cards)\n{link}")


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

            for table in ("token", "course", "report", "collection", "membership", "invite"):  # course_gone: by uid
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
    p.add_argument("action", choices=("create", "list", "revoke", "promote"))
    p.add_argument("id", nargs="?", type=int)
    p.add_argument("--label", help="a name to recognise the token, e.g. 'pixel'")
    p.set_defaults(func=cmd_token)

    p = sub.add_parser("courses", help="list shared courses")
    p.set_defaults(func=cmd_courses)

    p = sub.add_parser("owner", help="give courses to a token (the only one that can then change them)")
    p.add_argument("token_id", type=int)
    p.add_argument("uids", nargs="*", metavar="UID")
    p.add_argument("--from", dest="from_id", type=int, metavar="TOKEN_ID", help="every course of that token")
    p.add_argument("--unowned", action="store_true", help="every course without an owner")
    p.set_defaults(func=cmd_owner)

    p = sub.add_parser("import", help="share a course JSON file (flashcert-share-1 or flashcert-course-1)")
    p.add_argument("file")
    p.add_argument("--uid", help="course id; importing again with the same uid replaces it")
    p.add_argument("--certification")
    p.add_argument("--unlisted", action="store_true", help="reachable by link only, not on the list")
    p.add_argument("--owner", type=int, metavar="TOKEN_ID", help="the token whose app may then change it")
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
