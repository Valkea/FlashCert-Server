"""Settings, read from FLASHCERT_SHARE_* environment variables (a systemd EnvironmentFile works well)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PREFIX = "FLASHCERT_SHARE_"
#: What the app may offer on a shared page: take a copy, a copy that follows the author's changes, join the course to
#: study it, or join it to edit it. Editing comes only from the owner's own choice, and never on a public page.
LINK_ACTIONS = ("copy", "follow", "join", "edit")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(PREFIX + name)
    return value if value not in (None, "") else default


def link_actions(value: str | None) -> tuple[str, ...]:
    """The known actions in ``value`` ("copy,follow"), in their usual order."""
    chosen = {a.strip().lower() for a in (value or "").split(",")}
    return tuple(a for a in LINK_ACTIONS if a in chosen)


def default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "flashcert-share"


@dataclass
class Config:
    #: Any SQLAlchemy URL: sqlite:///path.db (default) or postgresql+psycopg://user:pass@host/db.
    database_url: str = ""
    #: Base of the share links handed to the app, e.g. https://courses.example.com. Taken from the
    #: request (and the proxy's X-Forwarded-* headers) when empty.
    public_url: str = ""
    #: Reverse proxies in front of the server whose X-Forwarded-* headers are trusted.
    proxies: int = 1
    #: Name shown on the course list and in page titles.
    site_name: str = "FlashCert"
    #: Largest course upload accepted from the app.
    max_upload_mb: int = 20
    #: Anonymous error reports allowed per visitor IP per hour.
    reports_per_hour: int = 20
    history_limit: int = 20
    trash_days: int = 30
    notes_change_percent: int = 30
    items_change_percent: int = 20
    #: The most a shared link may offer in the app; each owner can offer less per course or certification.
    link_actions: tuple[str, ...] = ("copy", "follow", "edit")
    #: Owners can invite people to their courses and certifications.
    invites: bool = True
    #: Joins (invite or link) allowed per visitor IP per hour.
    joins_per_hour: int = 10

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        data_dir = Path(_env("DATA_DIR") or default_data_dir())
        url = _env("DATABASE_URL")
        if not url:
            data_dir.mkdir(parents=True, exist_ok=True)
            url = "sqlite:///" + str(data_dir / "flashcert-share.db")
        cfg = cls(
            database_url=url,
            public_url=(_env("PUBLIC_URL") or "").rstrip("/"),
            proxies=int(_env("PROXIES", "1")),
            site_name=_env("SITE_NAME", "FlashCert"),
            max_upload_mb=int(_env("MAX_UPLOAD_MB", "20")),
            reports_per_hour=int(_env("REPORTS_PER_HOUR", "20")),
            history_limit=max(2, int(_env("HISTORY_LIMIT", "20"))),
            trash_days=max(1, int(_env("TRASH_DAYS", "30"))),
            notes_change_percent=max(1, min(100, int(_env("NOTES_CHANGE_PERCENT", "30")))),
            items_change_percent=max(1, min(100, int(_env("ITEMS_CHANGE_PERCENT", "20")))),
            link_actions=link_actions(_env("LINK_ACTIONS", "copy,follow,edit")),
            invites=_env("INVITES", "1") not in ("0", "false", "no", "off"),
            joins_per_hour=int(_env("JOINS_PER_HOUR", "10")),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(cfg, key, value)
        return cfg
