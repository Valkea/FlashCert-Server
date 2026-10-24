"""HTTP side: the sync API for the app (token), the public read-only pages and the anonymous error reports."""

from __future__ import annotations

import json
import re
import zlib
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from flask import Flask, abort, g, jsonify, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import __version__, changes, db, i18n, payload
from .config import LINK_ACTIONS, Config, link_actions
from .ratelimit import RateLimiter

UID = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
SHARE_ID = re.compile(r"^[A-Za-z0-9]{6,16}$")
REPORT_KINDS = ("question", "card", "section", "sheet", "course")
REPORT_STATUSES = ("open", "resolved", "dismissed")
GONE_REASONS = ("removed", "deleted")
MEMBER_ROLES = ("viewer", "editor")
#: What the owner decides for a course; members editing it send their content only.
OWNER_FIELDS = ("certification", "certification_uid", "position", "visibility")
#: Sync API versions: 1 recoverable sync of a token's own courses, 2 also the courses shared with it.
SYNC_VERSIONS = ("1", "2")
UI_LANGS = ("en", "fr")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def create_app(config: Config | None = None, engine: Engine | None = None) -> Flask:
    cfg = config or Config.from_env()
    engine = engine or db.make_engine(cfg.database_url)
    db.init(engine)
    db.prune(engine, cfg.history_limit, cfg.trash_days)

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_upload_mb * 1024 * 1024
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    app.extensions["flashcert"] = {"config": cfg, "engine": engine}
    if cfg.proxies > 0:
        n = cfg.proxies
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n, x_prefix=n)

    report_limiter = RateLimiter(cfg.reports_per_hour, 3600)
    join_limiter = RateLimiter(cfg.joins_per_hour, 3600)
    # Asset URLs carry ?v=<version>.<newest file time>, so browsers fetch new files after an upgrade.
    stamp = max(int(p.stat().st_mtime) for p in Path(app.static_folder).rglob("*") if p.is_file())
    asset_version = f"{__version__}.{stamp:x}"

    # --- helpers ---------------------------------------------------------------------------------

    def base_url() -> str:
        return cfg.public_url or request.url_root.rstrip("/")

    def share_url(share_id: str) -> str:
        if cfg.public_url:
            return f"{cfg.public_url}/c/{share_id}"
        return url_for("course_page", share_id=share_id, _external=True)

    def certification_url(share_id: str) -> str:
        if cfg.public_url:
            return f"{cfg.public_url}/cert/{share_id}"
        return url_for("certification_page", share_id=share_id, _external=True)

    def app_link(page_url: str) -> str:
        """Opens the page in the FlashCert app, which adds this server by itself."""
        return "flashcert://add?url=" + quote(page_url, safe="")

    def server_info() -> dict:
        return {"name": cfg.site_name, "url": base_url()}

    def actions(chosen: str | None, visibility: str | None) -> list[str]:
        """What a shared link offers in the app: the owner's choice within what the server allows. Editing is never
        part of the server's default, only of the owner's choice, and a public page, which anyone can find, never
        offers it."""
        if chosen is None:
            offered = [a for a in cfg.link_actions if a != "edit"]
        else:
            picked = link_actions(chosen)
            offered = [a for a in cfg.link_actions if a in picked]
        return [a for a in offered if a != "edit" or visibility != "public"]

    def stored_actions(value):
        """A link_actions value from the app (a list, or null for the server's default) as stored, or abort."""
        if value is None:
            return None
        if not isinstance(value, list) or any(a not in LINK_ACTIONS for a in value):
            abort(api_error(400, "link_actions must be null or a list among " + ", ".join(LINK_ACTIONS)))
        return ",".join(a for a in LINK_ACTIONS if a in value)

    def ui_lang() -> str:
        return request.accept_languages.best_match(UI_LANGS) or "en"

    def api_error(status: int, message: str, **extra):
        response = jsonify({"error": message, **extra})
        response.status_code = status
        return response

    def json_body() -> dict:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(api_error(400, "expected a JSON object body"))
        return data

    def bearer() -> str:
        header = request.headers.get("Authorization", "")
        return header[7:].strip() if header[:7].lower() == "bearer " else ""

    def unauthorized():
        response = api_error(401, "missing or invalid token")
        response.headers["WWW-Authenticate"] = 'Bearer realm="flashcert-share"'
        return response

    def require_token(view):
        """The token goes to g.token_id and g.token_kind: sync calls only see and touch its own courses and the
        ones shared with it."""
        @wraps(view)
        def wrapper(*args, **kwargs):
            token_id = db.check_token(engine, bearer())
            if token_id is None:
                return unauthorized()
            g.token_id = token_id
            g.token_kind = db.token_info(engine, token_id).kind
            return view(*args, **kwargs)

        return wrapper

    def sync_version() -> int:
        value = request.headers.get("X-FlashCert-Sync-Version")
        return int(value) if value in SYNC_VERSIONS else 0

    def course_row(**where):
        stmt = select(db.course)
        for key, value in where.items():
            stmt = stmt.where(db.course.c[key] == value)
        with engine.connect() as conn:
            return conn.execute(stmt).first()

    def my_course(uid: str, include_deleted: bool = False):
        """The course if this token owns it. Other people's courses answer 404, like missing ones."""
        row = course_row(uid=uid)
        return row if (row is not None and row.owner_id == g.token_id
                       and (include_deleted or row.deleted_at is None)) else None

    def course_for(uid: str, at_least: str, include_deleted: bool = False):
        """(row, role) when this token may at least ``at_least`` the course, else (None, None). Members only reach
        it through sync version 2, and never once it is deleted."""
        row = course_row(uid=uid)
        if row is None:
            return None, None
        with engine.connect() as conn:
            role = db.role_of(conn, g.token_id, row)
        if role != "owner" and sync_version() < 2:
            role = None
        if db.rank(role) < db.rank(at_least) or (row.deleted_at is not None and not include_deleted):
            return None, None
        return row, role

    def labels(ids) -> dict[int, str]:
        ids = {i for i in ids if i is not None}
        if not ids:
            return {}
        with engine.connect() as conn:
            return dict(conn.execute(select(db.token.c.id, db.token.c.label).where(db.token.c.id.in_(ids))).all())

    def reachable_reports() -> list[int]:
        """Courses whose reports this token reads: its own and the ones it may edit."""
        with engine.connect() as conn:
            owned = conn.execute(select(db.course.c.id).where(db.course.c.owner_id == g.token_id)).scalars().all()
            shared = db.shared_with(conn, g.token_id) if sync_version() >= 2 else {}
        return list(owned) + [cid for cid, role in shared.items() if role == "editor"]

    def expected(body: dict | None = None):
        value = (body or {}).get("expected_revision") if body is not None else request.args.get("expected_revision")
        if value is None:
            return None
        if body is None:
            try:
                value = int(value)
            except (ValueError, TypeError):
                abort(api_error(400, "expected_revision must be a nonnegative integer"))
        if type(value) is not int or value < 0:
            abort(api_error(400, "expected_revision must be a nonnegative integer"))
        return value

    def device():
        return request.headers.get("X-FlashCert-Device", "")[:100]

    def visible_course(share_id: str):
        """The course behind a public link; private courses (backups) have no page."""
        row = course_row(share_id=share_id) if SHARE_ID.match(share_id) else None
        return row if row is not None and row.visibility != "private" and row.deleted_at is None else None

    def visible_collection(share_id: str):
        """The certification behind a public link, unless its owner keeps it private."""
        if not SHARE_ID.match(share_id):
            return None
        with engine.connect() as conn:
            row = conn.execute(select(db.collection).where(db.collection.c.share_id == share_id)).first()
        return row if row is not None and row.visibility != "private" else None

    def open_reports_by_course() -> dict[int, int]:
        ids = reachable_reports()
        if not ids:
            return {}
        with engine.connect() as conn:
            rows = conn.execute(
                select(db.report.c.course_id, func.count())
                .join(db.course, db.course.c.id == db.report.c.course_id)
                .where(db.report.c.status == "open", db.course.c.id.in_(ids), db.course.c.deleted_at.is_(None))
                .group_by(db.report.c.course_id)
            )
            return {course_id: n for course_id, n in rows}

    def collection_summary(row) -> dict:
        with engine.connect() as conn:
            courses = db.collection_courses(conn, row, include_private=True)
        return {"uid": row.uid, "name": row.name, "share_id": row.share_id, "url": certification_url(row.share_id),
                "visibility": row.visibility, "courses": [c.uid for c in courses],
                "link_actions": None if row.link_actions is None else link_actions(row.link_actions),
                "actions": actions(row.link_actions, row.visibility)}

    def certification_name(row) -> str | None:
        """The current name of the course's certification: a course keeps the one it was sent with until it is sent
        again, after the certification was renamed."""
        uid = getattr(row, "certification_uid", None)
        if uid is None or row.owner_id is None:
            return row.certification
        with engine.connect() as conn:
            name = conn.execute(select(db.collection.c.name).where(db.collection.c.owner_id == row.owner_id,
                                                                   db.collection.c.uid == uid)).scalar_one_or_none()
        return row.certification if name is None else name

    def certification_link(row) -> str | None:
        """The page of the course's certification, when its owner shares one: where a visitor's mastered courses add up."""
        uid = getattr(row, "certification_uid", None)
        if uid is None or row.owner_id is None:
            return None
        with engine.connect() as conn:
            share_id = conn.execute(select(db.collection.c.share_id).where(
                db.collection.c.owner_id == row.owner_id, db.collection.c.uid == uid,
                db.collection.c.visibility != "private")).scalar_one_or_none()
        return None if share_id is None else url_for("certification_page", share_id=share_id)

    def course_summary(row, open_reports: int | None = None, role: str = "owner", owner: str | None = None) -> dict:
        out = {
            "uid": row.uid,
            "share_id": row.share_id,
            "url": share_url(row.share_id),
            "title": row.title,
            "certification": row.certification,
            "certification_uid": getattr(row, "certification_uid", None),
            "certification_name": certification_name(row),
            "lang": row.lang,
            "position": row.position,
            "visibility": row.visibility,
            "hash": row.content_hash,
            "questions": row.question_count,
            "cards": row.card_count,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
            "modified_at": iso(row.modified_at or row.updated_at),
            "revision": row.revision,
            "event": row.event,
            "deleted_at": iso(row.deleted_at),
            "role": role,
        }
        if owner is not None:
            out["owner"] = owner
        if role == "owner":
            chosen = getattr(row, "link_actions", None)
            out["link_actions"] = None if chosen is None else list(link_actions(chosen))
            out["actions"] = actions(chosen, row.visibility)
        if open_reports is not None:
            out["open_reports"] = open_reports
        return out

    def author(owner_id: int | None) -> str:
        """The name of who shares it, as they chose it in the app; empty for a course with no owner."""
        return labels([owner_id]).get(owner_id, "")

    def public_course(row) -> dict:
        return {
            "share_id": row.share_id,
            "url": share_url(row.share_id),
            "title": row.title,
            "author": author(row.owner_id),
            "certification": row.certification,
            "lang": row.lang,
            "position": row.position,
            "updated_at": iso(row.updated_at),
            "modified_at": iso(row.modified_at or row.updated_at),
            "revision": row.revision,
            "hash": row.content_hash,
            "actions": actions(row.link_actions, row.visibility),
            "server": server_info(),
            **payload.shown(json.loads(row.content)),
        }

    def report_out(row, uid: str | None = None) -> dict:
        out = {
            "id": row.id,
            "element": {"kind": row.element_kind, "id": row.element_id},
            "excerpt": row.excerpt,
            "message": row.message,
            "author": row.author,
            "status": row.status,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }
        if uid is not None:
            out["course_uid"] = uid
        return out

    def public_list() -> list:
        with engine.connect() as conn:
            rows = conn.execute(
                select(db.course.c.share_id, db.course.c.title, db.course.c.certification, db.course.c.lang,
                       db.course.c.question_count, db.course.c.card_count, db.course.c.updated_at,
                       db.course.c.position, db.course.c.owner_id)
                .where(db.course.c.visibility == "public", db.course.c.deleted_at.is_(None))
            ).all()
        rows.sort(key=lambda r: (r.certification is None, (r.certification or "").casefold(),
                                 r.position is None, r.position or 0, r.title.casefold()))
        return rows

    # --- errors and headers ----------------------------------------------------------------------

    @app.errorhandler(db.SyncConflict)
    def sync_conflict(e):
        return api_error(428 if e.code == "revision_required" else 409, str(e), code=e.code,
                         revision=e.revision, **e.details)

    @app.errorhandler(HTTPException)
    def http_error(e: HTTPException):
        if e.response is not None:
            return e.response
        if request.path.startswith("/api/"):
            return api_error(e.code or 500, e.description or e.name)
        if e.code == 404:
            return render_template("error.html"), 404
        return e

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.context_processor
    def template_globals():
        lang = ui_lang()
        return {"version": asset_version, "site_name": cfg.site_name, "lang": lang, "t": i18n.strings(lang)}

    # --- public pages ----------------------------------------------------------------------------

    @app.get("/")
    def index():
        groups: list[dict] = []
        rows = public_list()
        for row in rows:
            if not groups or groups[-1]["name"] != row.certification:
                groups.append({"name": row.certification, "courses": []})
            groups[-1]["courses"].append(row)
        return render_template("index.html", groups=groups, authors=labels(r.owner_id for r in rows),
                               day=lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat())

    @app.get("/c/<share_id>")
    @app.get("/c/<share_id>/<path:_slug>")
    def course_page(share_id: str, _slug: str = ""):
        row = visible_course(share_id)
        if row is None:
            abort(404)
        content = json.loads(row.content)
        sheet = content.get("sheet") or {}
        essentials = [e for e in sheet.get("essentiel", []) if isinstance(e, str)]
        description = essentials[0] if essentials else (row.certification or "")
        return render_template(
            "course.html",
            course=public_course(row),
            title=row.title,
            description=description[:300],
            url=share_url(row.share_id),
            unlisted=row.visibility != "public",
            app_link=app_link(share_url(row.share_id)) if actions(row.link_actions, row.visibility) else None,
            cert_link=certification_link(row),
        )

    @app.get("/cert/<share_id>")
    def certification_page(share_id: str):
        col = visible_collection(share_id)
        if col is None:
            abort(404)
        with engine.connect() as conn:
            courses = db.collection_courses(conn, col, include_private=False)
        url = certification_url(col.share_id)
        return render_template("certification.html", name=col.name, share_id=col.share_id, courses=courses, url=url,
                               author=author(col.owner_id), unlisted=col.visibility != "public",
                               app_link=app_link(url) if actions(col.link_actions, col.visibility) else None,
                               day=lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat())

    @app.get("/progress")
    def progress_page():
        """The visitor's own progress across courses, drawn from this browser's storage: the server knows none of it."""
        return render_template("progress.html")

    @app.get("/healthz")
    def healthz():
        with engine.connect() as conn:
            conn.execute(select(1))
        return {"ok": True, "version": __version__}

    # --- public API ------------------------------------------------------------------------------

    @app.get("/api/public/courses")
    def api_public_list():
        return {"courses": [{
            "share_id": r.share_id,
            "url": share_url(r.share_id),
            "title": r.title,
            "certification": r.certification,
            "lang": r.lang,
            "questions": r.question_count,
            "cards": r.card_count,
            "updated_at": iso(r.updated_at),
        } for r in public_list()]}

    @app.get("/api/public/courses/<share_id>")
    def api_public_course(share_id: str):
        """The course as visitors see it. The app copies it from here and follows its changes with If-None-Match."""
        row = visible_course(share_id)
        if row is None:
            return api_error(404, "no such course")
        body = public_course(row)
        response = jsonify(body)
        # Who shares it can rename themselves: the answer changes with them.
        signed = zlib.crc32(body["author"].encode("utf-8"))
        response.set_etag(f"{row.content_hash[:20]}-{row.revision}-{row.link_actions or ''}-{signed:x}")
        return response.make_conditional(request)

    @app.get("/api/public/certifications/<share_id>")
    def api_public_certification(share_id: str):
        col = visible_collection(share_id)
        if col is None:
            return api_error(404, "no such certification")
        with engine.connect() as conn:
            courses = db.collection_courses(conn, col, include_private=False)
        return {"share_id": col.share_id, "url": certification_url(col.share_id), "name": col.name,
                "author": author(col.owner_id), "actions": actions(col.link_actions, col.visibility),
                "server": server_info(),
                "courses": [{"share_id": r.share_id, "url": share_url(r.share_id), "title": r.title, "lang": r.lang,
                             "position": r.position, "questions": r.question_count, "cards": r.card_count,
                             "revision": r.revision, "hash": r.content_hash, "updated_at": iso(r.updated_at)}
                            for r in courses]}

    @app.post("/api/public/join")
    def api_join():
        """Joins a shared course or certification ({course} or {certification}: its share id) as an editor, when its
        link offers editing. With a token, that token gets the access; without one, a guest token is made and
        returned, once."""
        body = json_body()
        token_id = None
        if bearer():
            token_id = db.check_token(engine, bearer())
            if token_id is None:
                return unauthorized()
        name = body.get("name") or ""
        if not isinstance(name, str) or len(name) > 100:
            return api_error(400, "name must be at most 100 characters")
        target: dict = {}
        if isinstance(body.get("course"), str):
            row = visible_course(body["course"])
            if row is None:
                return api_error(404, "no such course")
            if "edit" not in actions(row.link_actions, row.visibility):
                return api_error(403, "this course can't be joined from its link")
            target["course_id"] = row.id
        elif isinstance(body.get("certification"), str):
            col = visible_collection(body["certification"])
            if col is None:
                return api_error(404, "no such certification")
            if "edit" not in actions(col.link_actions, col.visibility):
                return api_error(403, "this certification can't be joined from its link")
            target["collection_id"] = col.id
        else:
            return api_error(400, "send course or certification")
        if not join_limiter.allow(request.remote_addr or "?"):
            return api_error(429, "too many joins, try again later")
        try:
            token_id, raw, membership_id = db.join(engine, token_id, name.strip(), **target)
        except db.Gone:
            return api_error(404, "no such course")
        info = db.token_info(engine, token_id)
        out = {"token_id": token_id, "label": info.label, "kind": info.kind, "server": server_info(),
               "membership": membership_id}
        if raw is not None:
            out["token"] = raw
        return out, 201 if raw is not None else 200

    @app.post("/api/public/courses/<share_id>/reports")
    def api_report(share_id: str):
        row = visible_course(share_id)
        if row is None:
            return api_error(404, "no such course")
        body = json_body()
        # Honeypot: a field people never see, which form-filling bots fill.
        if body.get("website"):
            return {"ok": True}, 201
        element = body.get("element") or {}
        kind = element.get("kind") if isinstance(element, dict) else None
        element_id = str(element.get("id", "")) if isinstance(element, dict) else ""
        message = body.get("message")
        author = body.get("name") or ""
        if kind not in REPORT_KINDS:
            return api_error(400, "element.kind must be one of " + ", ".join(REPORT_KINDS))
        if not isinstance(message, str) or not 3 <= len(message.strip()) <= 2000:
            return api_error(400, "message must be 3 to 2000 characters")
        if not isinstance(author, str) or len(author) > 80:
            return api_error(400, "name must be at most 80 characters")
        snippet = payload.excerpt(json.loads(row.content), kind, element_id[:100], row.title)
        if snippet is None:
            return api_error(404, "no such element in this course")
        if not report_limiter.allow(request.remote_addr or "?"):
            return api_error(429, "too many reports, try again later")
        now = db.now_ms()
        with engine.begin() as conn:
            result = conn.execute(db.report.insert().values(
                course_id=row.id, element_kind=kind, element_id=element_id[:100], excerpt=snippet[:1000],
                message=message.strip(), author=author.strip(), status="open", created_at=now, updated_at=now))
        return {"ok": True, "id": result.inserted_primary_key[0]}, 201

    # --- sync API (the app) ----------------------------------------------------------------------

    @app.get("/api/v1/status")
    @require_token
    def api_status():
        with engine.connect() as conn:
            courses = conn.execute(select(func.count()).select_from(db.course)
                                   .where(db.course.c.owner_id == g.token_id, db.course.c.deleted_at.is_(None))).scalar_one()
            label = conn.execute(select(db.token.c.label).where(db.token.c.id == g.token_id)).scalar_one()
        reports = sum(open_reports_by_course().values())
        return {"ok": True, "version": __version__, "courses": courses, "open_reports": reports,
                "base_url": base_url(), "site_name": cfg.site_name, "token": label, "token_id": g.token_id,
                "kind": g.token_kind, "safe_sync": 1, "sync_version": 2, "certification_uids": 1,
                "link_actions": list(cfg.link_actions),
                "history_limit": cfg.history_limit, "trash_days": cfg.trash_days,
                "notes_change_percent": cfg.notes_change_percent, "items_change_percent": cfg.items_change_percent}

    @app.patch("/api/v1/me")
    @require_token
    def api_rename():
        """{name}: what other people see for this token, as the owner of what it shares, as a member and as the
        author of changes."""
        name = json_body().get("name")
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 100:
            return api_error(400, "name must be 1 to 100 characters")
        db.rename_token(engine, g.token_id, name.strip())
        return {"token_id": g.token_id, "name": name.strip()}

    @app.get("/api/v1/courses")
    @require_token
    def api_courses():
        counts = open_reports_by_course()
        with engine.connect() as conn:
            rows = conn.execute(select(db.course).where(db.course.c.owner_id == g.token_id, db.course.c.deleted_at.is_(None))
                                .order_by(db.course.c.id)).all()
            gone = conn.execute(select(db.course_gone).where(db.course_gone.c.owner_id == g.token_id)
                                .order_by(db.course_gone.c.deleted_at)).all()
        result = {"courses": [course_summary(r, counts.get(r.id, 0)) for r in rows]}
        # 0.2 clients hard-delete local recordings when they see a tombstone. Only negotiated safe clients
        # receive deletion instructions; the missing key makes older apps fall back to their one-way flow.
        if sync_version() >= 1:
            result["gone"] = [{"uid": r.uid, "reason": r.reason, "deleted_at": iso(r.deleted_at),
                               "revision": r.revision} for r in gone]
        # Version 2 also lists the courses shared with this token. When that ends (removed, deleted, made private
        # while joined from its link), "revoked" tells its devices to keep their copies, detached.
        if sync_version() >= 2:
            shared, roles, ended = db.member_courses(engine, g.token_id)
            owners = labels(r.owner_id for r in shared)
            result["courses"] += [course_summary(r, counts.get(r.id, 0) if roles[r.id] == "editor" else None,
                                                 roles[r.id], owners.get(r.owner_id, "")) for r in shared]
            result["gone"] += [{"uid": r.uid, "reason": "revoked", "deleted_at": None, "revision": r.revision}
                               for r in ended]
        return result

    @app.put("/api/v1/courses/<uid>")
    @require_token
    def api_put_course(uid: str):
        if not UID.match(uid):
            return api_error(400, "uid must be 1-100 characters among A-Z a-z 0-9 . _ : -")
        try:
            doc = json_body()
            meta, content, warnings = payload.normalize(doc)
        except payload.PayloadError as e:
            return api_error(422, str(e))
        owner_id = g.token_id
        existing = course_row(uid=uid)
        if existing is None and g.token_kind == "guest":
            return api_error(403, "a guest token only works on the courses shared with it", code="guest")
        if existing is not None and existing.owner_id not in (None, g.token_id):
            existing, role = course_for(uid, "viewer")
            if role is None:
                return api_error(403, "this course belongs to another token")
            if role != "editor":
                return api_error(403, "this course is shared with you to read only", code="read_only")
            # The owner decides where the course is listed and who sees it; members send their content.
            owner_id = existing.owner_id
            meta.update({k: existing._mapping[k] for k in OWNER_FIELDS})
        elif meta["certification"] is not None:
            # The certification the server knows by this id, or by this name (older apps, or another device's id).
            with engine.begin() as conn:
                col = db.certification_for(conn, owner_id, meta["certification_uid"], meta["certification"],
                                           create=meta["certification_uid"] is not None)
            meta["certification_uid"] = col.uid if col is not None else None
        now = db.now_ms()
        values = {
            **meta,
            # Keep edit timestamps plausible; revisions decide conflicts.
            "modified_at": min(meta["modified_at"] or now, now),
            "content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
            "content_hash": payload.content_hash(meta, content),
            "question_count": len(content["questions"]),
            "card_count": len(content["cards"]),
        }
        try:
            created, changed = db.save_course(engine, uid, values, owner_id, expected=expected(doc),
                                              confirmation=doc.get("confirmation"), device=device(),
                                              notes_limit=cfg.notes_change_percent, items_limit=cfg.items_change_percent,
                                              author=g.token_id)
        except db.NotYours:
            return api_error(403, "this course belongs to another token")
        db.prune(engine, cfg.history_limit, cfg.trash_days)
        row = course_row(uid=uid)
        summary = course_summary(row) if owner_id == g.token_id else \
            course_summary(row, role="editor", owner=labels([row.owner_id]).get(row.owner_id, ""))
        body = {**summary, "created": created, "changed": changed, "warnings": warnings}
        return body, 201 if created else 200

    @app.get("/api/v1/courses/<uid>")
    @require_token
    def api_get_course(uid: str):
        row, role = course_for(uid, "viewer")
        if row is None:
            return api_error(404, "no such course")
        if sync_version() < 1:
            return api_error(428, "upgrade the client to recoverable sync before downloading course replacements",
                             code="client_upgrade_required")
        owner = None if role == "owner" else labels([row.owner_id]).get(row.owner_id, "")
        return {**course_summary(row, role=role, owner=owner), "content": json.loads(row.content)}

    @app.post("/api/v1/courses/<uid>/preview")
    @require_token
    def api_preview(uid):
        row, role = course_for(uid, "editor")
        if row is None:
            return api_error(404, "no such course")
        doc = json_body()
        db._expected(row, expected(doc))
        try:
            meta, content, warnings = payload.normalize(doc)
        except payload.PayloadError as e:
            return api_error(422, str(e))
        if role != "owner":
            meta.update({k: row._mapping[k] for k in OWNER_FIELDS})
        impact = changes.summarize(json.loads(row.content), content, cfg.notes_change_percent, cfg.items_change_percent)
        return {"revision": row.revision, "changes": impact, "confirmation": payload.confirmation_hash(meta, content),
                "warnings": warnings}

    @app.get("/api/v1/trash")
    @require_token
    def api_trash():
        with engine.connect() as conn:
            rows = conn.execute(select(db.course).where(db.course.c.owner_id == g.token_id,
                                                        db.course.c.deleted_at.is_not(None))
                                .order_by(db.course.c.deleted_at.desc())).all()
        return {"courses": [course_summary(row) for row in rows]}

    @app.get("/api/v1/courses/<uid>/history")
    @require_token
    def api_history(uid):
        row, _ = course_for(uid, "viewer", include_deleted=True)
        if row is None or (row.deleted_at is not None and row.owner_id != g.token_id):
            return api_error(404, "no such course")
        with engine.connect() as conn:
            revisions = conn.execute(select(db.course_revision).where(db.course_revision.c.course_id == row.id)
                                     .order_by(db.course_revision.c.revision)).all()
        authors = labels(r.token_id for r in revisions)
        history, previous = [], None
        for revision in revisions:
            saved = json.loads(revision.snapshot)
            content = json.loads(saved["content"])
            history.append({"revision": revision.revision, "action": revision.action, "device": revision.device,
                            "author": authors.get(revision.token_id, ""),
                            "created_at": iso(revision.created_at), "title": saved["title"],
                            "changes": changes.summarize(previous, content) if previous is not None else None})
            previous = content
        return {"revision": row.revision, "history": history[::-1]}

    @app.get("/api/v1/courses/<uid>/history/<int:revision>")
    @require_token
    def api_revision(uid, revision):
        row, role = course_for(uid, "viewer", include_deleted=True)
        if row is None or (row.deleted_at is not None and row.owner_id != g.token_id):
            return api_error(404, "no such course")
        with engine.connect() as conn:
            saved = conn.execute(select(db.course_revision.c.snapshot)
                                 .where(db.course_revision.c.course_id == row.id,
                                        db.course_revision.c.revision == revision)).scalar_one_or_none()
        if saved is None:
            return api_error(404, "no such revision")
        saved = json.loads(saved)
        return {**course_summary(SimpleNamespace(**saved), role=role), "content": json.loads(saved["content"])}

    @app.post("/api/v1/courses/<uid>/restore")
    @require_token
    def api_restore(uid):
        row, role = course_for(uid, "editor", include_deleted=True)
        if row is None or (row.deleted_at is not None and role != "owner"):
            return api_error(404, "no such course")
        body = json_body()
        revision = body.get("revision")
        if type(revision) is not int or revision < 1:
            return api_error(400, "revision must be a positive integer")
        visibility = body.get("visibility")
        if visibility is not None and visibility not in payload.VISIBILITIES:
            return api_error(400, "invalid restore visibility")
        if role != "owner":
            visibility = row.visibility
        db.restore_course(engine, uid, row.owner_id, revision, expected(body), body.get("confirmation"), device(),
                          visibility if role == "owner" else None, author=g.token_id)
        db.prune(engine, cfg.history_limit, cfg.trash_days)
        row, role = course_for(uid, "viewer")
        return course_summary(row, role=role)

    @app.patch("/api/v1/courses/<uid>")
    @require_token
    def api_patch_course(uid: str):
        """The owner's settings: {visibility, expected_revision} and/or {link_actions}."""
        body = json_body()
        if "visibility" not in body and "link_actions" not in body:
            return api_error(400, "send visibility or link_actions")
        visibility = body.get("visibility")
        if "visibility" in body and visibility not in payload.VISIBILITIES:
            return api_error(400, "visibility must be public, unlisted or private")
        row = my_course(uid)
        if row is None:
            return api_error(404, "no such course")
        if "link_actions" in body:
            chosen = stored_actions(body["link_actions"])
            with engine.begin() as conn:
                conn.execute(db.course.update().where(db.course.c.id == row.id).values(link_actions=chosen))
        if "visibility" in body:
            values = {k: row._mapping[k] for k in ("title", "certification", "certification_uid", "lang", "position",
                                                  "content", "content_hash", "modified_at", "question_count",
                                                  "card_count")}
            values["visibility"] = visibility
            db.save_course(engine, uid, values, g.token_id, expected=expected(body), device=device())
        return course_summary(course_row(uid=uid))

    @app.delete("/api/v1/courses/<uid>")
    @require_token
    def api_delete_course(uid: str):
        reason = request.args.get("reason", "removed")
        if reason not in GONE_REASONS:
            return api_error(400, "reason must be removed or deleted")
        row = my_course(uid, True)
        if row is None:
            return api_error(404, "no such course")
        db.delete_course(engine, row.id, uid, g.token_id, reason, expected=expected(),
                         confirmation=request.args.get("confirmation"), device=device())
        return "", 204

    # --- sharing with other people (owner side, and the member's own memberships) -------------------

    def certification_uid(value) -> str | None:
        """The app's id for a certification, from a body or a query, or abort."""
        if value in (None, ""):
            return None
        if not isinstance(value, str) or not UID.match(value):
            abort(api_error(400, "a certification uid must be 1-100 characters among A-Z a-z 0-9 . _ : -"))
        return value

    def target_from(source: dict):
        """(course row, collection row) named by {course: uid} or {certification: name, uid}, owned by this token.
        A certification is shared as a whole by its uid (else its name); it is made the first time."""
        if isinstance(source.get("course"), str):
            row = my_course(source["course"])
            if row is None:
                abort(api_error(404, "no such course"))
            return row, None
        name = source.get("certification")
        if isinstance(name, str) and 0 < len(name.strip()) <= 200:
            with engine.begin() as conn:
                return None, db.certification_for(conn, g.token_id, certification_uid(source.get("uid")), name.strip(),
                                                  create=True)
        abort(api_error(400, "name a course (uid) or a certification (name)"))

    def membership_out(m, conn) -> dict:
        out = {"id": m.id, "role": m.role, "created_at": iso(m.created_at)}
        if m.course_id is not None:
            row = conn.execute(select(db.course).where(db.course.c.id == m.course_id)).first()
            out["course"] = {"uid": row.uid, "title": row.title, "certification": row.certification}
            owner = row.owner_id
        else:
            col = conn.execute(select(db.collection).where(db.collection.c.id == m.collection_id)).first()
            out["certification"] = {"uid": col.uid, "name": col.name, "share_id": col.share_id}
            owner = col.owner_id
        out["owner"] = conn.execute(select(db.token.c.label).where(db.token.c.id == owner)).scalar_one_or_none() or ""
        return out

    @app.get("/api/v1/memberships")
    @require_token
    def api_memberships():
        """What other people shared with this token."""
        with engine.connect() as conn:
            rows = conn.execute(select(db.membership).where(db.membership.c.token_id == g.token_id)
                                .order_by(db.membership.c.id)).all()
            return {"memberships": [membership_out(m, conn) for m in rows]}

    @app.get("/api/v1/members")
    @require_token
    def api_members():
        """Who the owner shared a course (?course=uid) or a certification (?certification=name, and its
        &certification_uid) with."""
        if request.args.get("course"):
            row = my_course(request.args["course"])
            if row is None:
                return api_error(404, "no such course")
            return {"members": db.members(engine, g.token_id, course_id=row.id)}
        with engine.connect() as conn:
            col = db.certification_for(conn, g.token_id, certification_uid(request.args.get("certification_uid")),
                                       request.args.get("certification", "").strip())
        return {"members": db.members(engine, g.token_id, collection_id=col.id) if col is not None else []}

    @app.patch("/api/v1/memberships/<int:membership_id>")
    @require_token
    def api_patch_membership(membership_id: int):
        role = json_body().get("role")
        if role not in MEMBER_ROLES:
            return api_error(400, "role must be viewer or editor")
        with engine.begin() as conn:
            found = db.membership_owner(conn, membership_id)
            if found is None or found[1] != g.token_id:
                return api_error(404, "no such member")
            conn.execute(db.membership.update().where(db.membership.c.id == membership_id).values(role=role))
        return {"id": membership_id, "role": role}

    @app.delete("/api/v1/memberships/<int:membership_id>")
    @require_token
    def api_delete_membership(membership_id: int):
        """The owner removes a member, or the member leaves. Their devices keep their copies, detached."""
        with engine.begin() as conn:
            found = db.membership_owner(conn, membership_id)
            if found is None or g.token_id not in (found[0].token_id, found[1]):
                return api_error(404, "no such member")
            conn.execute(db.membership.delete().where(db.membership.c.id == membership_id))
        return "", 204

    @app.get("/api/v1/certifications")
    @require_token
    def api_certifications():
        """The owner's certifications shared as a whole, or that have members."""
        with engine.connect() as conn:
            joined = select(db.membership.c.collection_id).where(db.membership.c.collection_id.is_not(None))
            rows = conn.execute(select(db.collection).where(db.collection.c.owner_id == g.token_id)
                                .where((db.collection.c.visibility != "private") | db.collection.c.id.in_(joined))
                                .order_by(db.collection.c.name)).all()
        return {"certifications": [collection_summary(r) for r in rows]}

    @app.put("/api/v1/certifications/<uid>")
    @require_token
    def api_name_certification(uid: str):
        """{name, previous}: the app renamed the certification ``uid`` from ``previous`` (null: it only tells the
        server its id). Its link and the people it is shared with stay. The answer is the certification as it is now:
        its name is another device's when that one renamed it since, its uid the server's when another device sent
        it first."""
        uid = certification_uid(uid)
        body = json_body()
        name, previous = body.get("name"), body.get("previous")
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 200:
            return api_error(400, "name must be 1 to 200 characters")
        if previous is not None and not isinstance(previous, str):
            return api_error(400, "previous must be a string or null")
        if g.token_kind == "guest":
            return api_error(403, "a guest token only works on the courses shared with it", code="guest")
        try:
            with engine.begin() as conn:
                col = db.rename_certification(conn, g.token_id, uid, name.strip(),
                                              previous.strip() if previous else None)
        except db.NameTaken:
            return api_error(409, "another certification has this name", code="name_taken", name=name.strip())
        return collection_summary(col)

    @app.put("/api/v1/certifications")
    @require_token
    def api_share_certification():
        """{name, uid, visibility, link_actions}: a page for the certification (its courses that aren't private), or
        "private" for none. The link stays the same."""
        body = json_body()
        visibility = body.get("visibility", "unlisted")
        if visibility not in payload.VISIBILITIES:
            return api_error(400, "visibility must be public, unlisted or private")
        if g.token_kind == "guest":
            return api_error(403, "a guest token only works on the courses shared with it", code="guest")
        _, col = target_from({"certification": body.get("name"), "uid": body.get("uid")})
        values = {"visibility": visibility}
        if "link_actions" in body:
            values["link_actions"] = stored_actions(body["link_actions"])
        with engine.begin() as conn:
            conn.execute(db.collection.update().where(db.collection.c.id == col.id).values(**values))
            col = conn.execute(select(db.collection).where(db.collection.c.id == col.id)).one()
        return collection_summary(col)

    def my_report(report_id: int):
        with engine.connect() as conn:
            return conn.execute(select(db.report, db.course.c.uid)
                                .join(db.course, db.course.c.id == db.report.c.course_id)
                                .where(db.report.c.id == report_id, db.course.c.id.in_(reachable_reports()))).first()

    @app.get("/api/v1/reports")
    @require_token
    def api_reports():
        stmt = (select(db.report, db.course.c.uid)
                .join(db.course, db.course.c.id == db.report.c.course_id)
                .where(db.course.c.id.in_(reachable_reports()))
                .order_by(db.report.c.id))
        status = request.args.get("status", "open")
        if status != "all":
            if status not in REPORT_STATUSES:
                return api_error(400, "status must be open, resolved, dismissed or all")
            stmt = stmt.where(db.report.c.status == status)
        if request.args.get("course"):
            stmt = stmt.where(db.course.c.uid == request.args["course"])
        since = request.args.get("since_id", "")
        if since.isdigit():
            stmt = stmt.where(db.report.c.id > int(since))
        with engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return {"reports": [report_out(r, r.uid) for r in rows]}

    @app.patch("/api/v1/reports/<int:report_id>")
    @require_token
    def api_patch_report(report_id: int):
        status = json_body().get("status")
        if status not in REPORT_STATUSES:
            return api_error(400, "status must be open, resolved or dismissed")
        if my_report(report_id) is None:
            return api_error(404, "no such report")
        with engine.begin() as conn:
            conn.execute(db.report.update().where(db.report.c.id == report_id)
                         .values(status=status, updated_at=db.now_ms()))
        row = my_report(report_id)
        return report_out(row, row.uid)

    @app.delete("/api/v1/reports/<int:report_id>")
    @require_token
    def api_delete_report(report_id: int):
        if my_report(report_id) is None:
            return api_error(404, "no such report")
        with engine.begin() as conn:
            conn.execute(db.report.delete().where(db.report.c.id == report_id))
        return "", 204

    return app
