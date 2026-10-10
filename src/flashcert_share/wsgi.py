"""WSGI entry point for other servers, e.g. `gunicorn -w 2 flashcert_share.wsgi:app` (settings from the environment)."""

from .app import create_app

app = create_app()
