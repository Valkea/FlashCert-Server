"""HTTP side: the sync API for the app (token), the public read-only pages and the anonymous error reports."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, abort, g, jsonify, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import __version__, changes, db, i18n, payload
from .config import Config
from .ratelimit import RateLimiter

UID = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
SHARE_ID = re.compile(r"^[A-Za-z0-9]{6,16}$")
REPORT_KINDS = ("question", "card", "section", "sheet", "course")
REPORT_STATUSES = ("open", "resolved", "dismissed")
GONE_REASONS = ("removed", "deleted")
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

    def require_token(view):
        """The token's id goes to g.token_id: sync calls only see and touch that token's courses."""
        @wraps(view)
        def wrapper(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            raw = header[7:].strip() if header[:7].lower() == "bearer " else ""
            token_id = db.check_token(engine, raw)
            if token_id is None:
                response = api_error(401, "missing or invalid token")
                response.headers["WWW-Authenticate"] = 'Bearer realm="flashcert-share"'
                return response
            g.token_id = token_id
            return view(*args, **kwargs)

        return wrapper

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

    def open_reports_by_course() -> dict[int, int]:
        with engine.connect() as conn:
            rows = conn.execute(
                select(db.report.c.course_id, func.count())
                .join(db.course, db.course.c.id == db.report.c.course_id)
                .where(db.report.c.status == "open", db.course.c.owner_id == g.token_id, db.course.c.deleted_at.is_(None))
                .group_by(db.report.c.course_id)
            )
            return {course_id: n for course_id, n in rows}

    def course_summary(row, open_reports: int | None = None) -> dict:
        out = {
            "uid": row.uid,
            "share_id": row.share_id,
            "url": share_url(row.share_id),
            "title": row.title,
            "certification": row.certification,
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
        }
        if open_reports is not None:
            out["open_reports"] = open_reports
        return out

    def public_course(row) -> dict:
        return {
            "share_id": row.share_id,
            "title": row.title,
            "certification": row.certification,
            "lang": row.lang,
            "updated_at": iso(row.updated_at),
            **json.loads(row.content),
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
                       db.course.c.position)
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
        for row in public_list():
            if not groups or groups[-1]["name"] != row.certification:
                groups.append({"name": row.certification, "courses": []})
            groups[-1]["courses"].append(row)
        return render_template("index.html", groups=groups,
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
        )

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
        row = visible_course(share_id)
        if row is None:
            return api_error(404, "no such course")
        return public_course(row)

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
                "base_url": base_url(), "token": label, "token_id": g.token_id, "safe_sync": 1,
                "history_limit": cfg.history_limit, "trash_days": cfg.trash_days,
                "notes_change_percent": cfg.notes_change_percent, "items_change_percent": cfg.items_change_percent}

    @app.get("/api/v1/courses")
    @require_token
    def api_courses():
        counts = open_reports_by_course()
        with engine.connect() as conn:
            rows = conn.execute(select(db.course).where(db.course.c.owner_id == g.token_id, db.course.c.deleted_at.is_(None))
                                .order_by(db.course.c.id)).all()
            gone = conn.execute(select(db.course_gone).where(db.course_gone.c.owner_id == g.token_id)
                                .order_by(db.course_gone.c.deleted_at)).all()
        return {"courses": [course_summary(r, counts.get(r.id, 0)) for r in rows],
                "gone": [{"uid": r.uid, "reason": r.reason, "deleted_at": iso(r.deleted_at),
                          "revision": r.revision} for r in gone]}

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
        now = db.now_ms()
        values = {
            **meta,
            # A device whose clock runs ahead would otherwise win every conflict.
            "modified_at": min(meta["modified_at"] or now, now),
            "content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
            "content_hash": payload.content_hash(meta, content),
            "question_count": len(content["questions"]),
            "card_count": len(content["cards"]),
        }
        try:
            created, changed = db.save_course(engine, uid, values, g.token_id, expected=expected(doc),
                                              confirmation=doc.get("confirmation"), device=device(),
                                              notes_limit=cfg.notes_change_percent, items_limit=cfg.items_change_percent)
        except db.NotYours:
            return api_error(403, "this course belongs to another token")
        db.prune(engine, cfg.history_limit, cfg.trash_days)
        body = {**course_summary(course_row(uid=uid)), "created": created, "changed": changed, "warnings": warnings}
        return body, 201 if created else 200

    @app.get("/api/v1/courses/<uid>")
    @require_token
    def api_get_course(uid: str):
        row = my_course(uid)
        if row is None:
            return api_error(404, "no such course")
        return {**course_summary(row), "content": json.loads(row.content)}

    @app.post("/api/v1/courses/<uid>/preview")
    @require_token
    def api_preview(uid):
        row = my_course(uid)
        if row is None:
            return api_error(404, "no such course")
        doc = json_body()
        db._expected(row, expected(doc))
        try:
            meta, content, warnings = payload.normalize(doc)
        except payload.PayloadError as e:
            return api_error(422, str(e))
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
        row = my_course(uid, True)
        if row is None:
            return api_error(404, "no such course")
        with engine.connect() as conn:
            revisions = conn.execute(select(db.course_revision).where(db.course_revision.c.course_id == row.id)
                                     .order_by(db.course_revision.c.revision)).all()
        history, previous = [], None
        for revision in revisions:
            saved = json.loads(revision.snapshot)
            content = json.loads(saved["content"])
            history.append({"revision": revision.revision, "action": revision.action, "device": revision.device,
                            "created_at": iso(revision.created_at), "title": saved["title"],
                            "changes": changes.summarize(previous, content) if previous is not None else None})
            previous = content
        return {"revision": row.revision, "history": history[::-1]}

    @app.get("/api/v1/courses/<uid>/history/<int:revision>")
    @require_token
    def api_revision(uid, revision):
        row = my_course(uid, True)
        if row is None:
            return api_error(404, "no such course")
        with engine.connect() as conn:
            saved = conn.execute(select(db.course_revision.c.snapshot)
                                 .where(db.course_revision.c.course_id == row.id,
                                        db.course_revision.c.revision == revision)).scalar_one_or_none()
        if saved is None:
            return api_error(404, "no such revision")
        saved = json.loads(saved)
        return {**course_summary(SimpleNamespace(**saved)), "content": json.loads(saved["content"])}

    @app.post("/api/v1/courses/<uid>/restore")
    @require_token
    def api_restore(uid):
        if my_course(uid, True) is None:
            return api_error(404, "no such course")
        body = json_body()
        revision = body.get("revision")
        if type(revision) is not int or revision < 1:
            return api_error(400, "revision must be a positive integer")
        db.restore_course(engine, uid, g.token_id, revision, expected(body), body.get("confirmation"), device())
        db.prune(engine, cfg.history_limit, cfg.trash_days)
        return course_summary(my_course(uid))

    @app.patch("/api/v1/courses/<uid>")
    @require_token
    def api_patch_course(uid: str):
        body = json_body()
        visibility = body.get("visibility")
        if visibility not in payload.VISIBILITIES:
            return api_error(400, "visibility must be public, unlisted or private")
        row = my_course(uid)
        if row is None:
            return api_error(404, "no such course")
        values = {k: row._mapping[k] for k in ("title", "certification", "lang", "position", "content", "content_hash",
                                              "modified_at", "question_count", "card_count")}
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

    def my_report(report_id: int):
        with engine.connect() as conn:
            return conn.execute(select(db.report, db.course.c.uid)
                                .join(db.course, db.course.c.id == db.report.c.course_id)
                                .where(db.report.c.id == report_id, db.course.c.owner_id == g.token_id)).first()

    @app.get("/api/v1/reports")
    @require_token
    def api_reports():
        stmt = (select(db.report, db.course.c.uid)
                .join(db.course, db.course.c.id == db.report.c.course_id)
                .where(db.course.c.owner_id == g.token_id)
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
