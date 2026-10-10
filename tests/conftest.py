import copy
import os

import pytest
from sqlalchemy import create_engine

from flashcert_share import db
from flashcert_share.app import create_app
from flashcert_share.config import Config

NOTES = """## Guardrails

### What a guardrail is
- **Guardrail**: a control that denies or gates a dangerous action **before** it runs.

> A guardrail only counts if it acts before execution.

### Least privilege
Give the agent only the tools it needs.

## Lifecycle events

| Event | When |
|---|---|
| `PreToolUse` | Before execution |
| `PostToolUse` | After execution |

## Exam focus
- PreToolUse is the only event that can prevent an action.
"""

COURSE = {
    "format": "flashcert-share-1",
    "title": "Hooks and guardrails",
    "certification": "Claude Agent SDK",
    "lang": "en",
    "visibility": "public",
    "position": 2,
    "notes": NOTES,
    "sheet": {
        "essentiel": ["Only PreToolUse can prevent an action."],
        "definitions": [{"terme": "Guardrail", "definition": "A control that acts before execution."}],
        "structure": [{"titre": "Events", "type": "tableau", "colonnes": ["Event", "When"],
                       "items": [{"cellules": ["PreToolUse", "Before"]}]}],
        "pieges": [{"confusion": "PreToolUse vs PostToolUse", "precision": "Only the first one can block."}],
    },
    "questions": [
        {"id": "q1", "type": "mcq", "q": "Which event can block?", "answers": ["PostToolUse", "PreToolUse"],
         "correct": 1, "explanation": "It runs before.", "notion": "Blocking",
         "sources": [{"section_id": "1.1", "quote": "denies or gates a dangerous action", "verified": True}]},
        {"id": "q2", "type": "multi", "q": "Which are true?", "options": ["A", "B", "C"], "correct": [0, 2]},
        {"id": "q3", "type": "true_false", "q": "Hooks can be prompted away.", "isTrue": False},
        {"id": "q4", "type": "cloze", "q": "Exit code ＿＿＿ blocks.", "answer": "2", "accept": ["two"], "bank": ["0", "1"]},
        {"id": "q5", "type": "order", "q": "Order these.", "items": ["Match", "Inspect", "Deny"]},
    ],
    "cards": [
        {"id": "c1", "front": "What is a guardrail?", "back": "A control that acts before execution.",
         "source": {"section_id": "1.1", "quote": "a control that denies"}},
        {"id": "c2", "front": "Least privilege?", "back": "Only the tools needed."},
    ],
}


@pytest.fixture
def course():
    return copy.deepcopy(COURSE)


def database_url(tmp_path) -> str:
    """SQLite by default; FLASHCERT_SHARE_TEST_DATABASE_URL runs the suite on another database (emptied first)."""
    url = os.environ.get("FLASHCERT_SHARE_TEST_DATABASE_URL")
    if not url:
        return f"sqlite:///{tmp_path / 'test.db'}"
    engine = create_engine(url)
    db.metadata.drop_all(engine)
    engine.dispose()
    return url


@pytest.fixture
def app(tmp_path):
    cfg = Config(database_url=database_url(tmp_path), public_url="https://share.example.com",
                 proxies=0, reports_per_hour=3)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def engine(app):
    return app.extensions["flashcert"]["engine"]


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth(engine):
    _, raw = db.create_token(engine, "test")
    return {"Authorization": f"Bearer {raw}", "X-FlashCert-Sync-Version": "1"}
