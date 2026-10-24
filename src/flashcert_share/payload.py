"""The course document the app uploads, checked and reduced to what the web pages show.

Format "flashcert-share-1" (what the app sends)::

    {
      "format": "flashcert-share-1",
      "title": "Hooks and guardrails",
      "certification": "Claude Agent SDK",      # optional
      "certification_uid": "5f0c…",             # optional, the app's id for it, kept when it is renamed
      "lang": "en",                             # optional, language of the notes
      "visibility": "public",                   # or "unlisted" (link only) or "private" (backup, no page)
      "position": 3,                            # optional, order inside the certification
      "modified_at": 1760000000000,             # optional, last edit on the device (ms since the epoch)
      "notes": "## Section\\n...",              # Markdown
      "sheet": {...},                           # optional revision sheet
      "questions": [{"id": "q12", "type": "mcq", "q": "...", ...}],
      "cards": [{"id": "c7", "front": "...", "back": "...", "sources": [...]}],
      "notions": [{"chapter": "Section", "quote": "...", "importance": 3}],  # optional
      "review_targets": {"questions": 3, "cards": 3}                       # optional
    }

Questions and cards may carry "objective_ids": the app's coverage checklist notions they test. The server keeps
them for the app and leaves them out of the pages. With a checklist, they also carry "importance": the highest
level of the notions they test, 3 essential, 2 important, 1 detail, 0 none. "notions" lists the checklist's
notions by "##" chapter heading, with a quote of the notes, so the pages can dim the lines of other levels.
"review_targets" are the successes in a row the course's certification asks of each question (1 or more) and card
(0 leaves cards out of mastery), so visitors master the course as its author's app counts it.
A valid item comes back exactly as it was sent, so the app can compare its copy with the server's.

"flashcert-course-1" bundles (full course exports) are accepted too (transcript, analysis and costs are dropped).
"""

from __future__ import annotations

import hashlib
import json
import re

FORMATS = ("flashcert-share-1", "flashcert-course-1")
VISIBILITIES = ("public", "unlisted", "private")
QUESTION_TYPES = ("mcq", "multi", "true_false", "cloze", "order")

MAX_QUESTIONS = 2000
MAX_CARDS = 5000
MAX_SOURCES = 3
MAX_OBJECTIVE_IDS = 50
MAX_NOTIONS = 5000
IMPORTANCE_LEVELS = (0, 1, 2, 3)
MAX_REVIEW_TARGET = 100

_QUESTION_KEYS = {
    "mcq": ("answers", "correct"),
    "multi": ("options", "correct"),
    "true_false": ("isTrue",),
    "cloze": ("answer", "accept", "bank"),
    "order": ("items",),
}
_COMMON_QUESTION_KEYS = ("explanation", "notion", "difficulty")
# The app's own bookkeeping on an item: kept for it, never shown, and an item edit when only these change isn't one.
METADATA_KEYS = ("objective_ids", "importance")
HIDDEN_KEYS = ("objective_ids",)


class PayloadError(ValueError):
    pass


def _text(value, limit: int, name: str, required: bool = False, strip: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise PayloadError(f"{name} must be a string")
    if strip:
        value = value.strip()
    if required and not value.strip():
        raise PayloadError(f"{name} is required")
    if len(value) > limit:
        raise PayloadError(f"{name} is longer than {limit} characters")
    return value


def _strings(value) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return value


def _sources(item: dict) -> list[dict]:
    """The item's citations as a list, accepting the older single "source" object."""
    raw = item.get("sources")
    if raw is None:
        raw = item.get("source")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        section = s.get("section_id")
        quote = s.get("quote")
        clean = {
            "section_id": str(section).replace("§", "").strip()[:20] if section is not None else "",
            "quote": quote[:1000] if isinstance(quote, str) else "",
        }
        if isinstance(s.get("verified"), bool):
            clean["verified"] = s["verified"]
        out.append(clean)
    return out[:MAX_SOURCES]


def _objective_ids(item: dict) -> list[str] | None:
    """The coverage checklist notions the item tests, as the app sent them; None when absent or malformed."""
    ids = _strings(item.get("objective_ids"))
    if ids is None or len(ids) > MAX_OBJECTIVE_IDS or any(len(i) > 200 for i in ids):
        return None
    return ids


def _metadata(item: dict, out: dict) -> dict:
    """Adds the item's citations, when it has a field for them, its checklist ids and its importance."""
    if "sources" in item or "source" in item:
        out["sources"] = _sources(item)
    ids = _objective_ids(item)
    if ids is not None:
        out["objective_ids"] = ids
    level = item.get("importance")
    if type(level) is int and level in IMPORTANCE_LEVELS:
        out["importance"] = level
    return out


def _item_id(raw, fallback: str, seen: set[str]) -> str:
    base = str(raw).strip()[:64] if isinstance(raw, (str, int)) and str(raw).strip() else fallback
    candidate, n = base, 2
    while candidate in seen:
        candidate = f"{base}-{n}"
        n += 1
    seen.add(candidate)
    return candidate


def _digest(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def _question(q: dict) -> dict | None:
    """A clean copy of a question, or None when it can't be played."""
    kind = q.get("type")
    stem = q.get("q")
    if kind not in QUESTION_TYPES or not isinstance(stem, str) or not stem.strip():
        return None
    out = {"type": kind, "q": stem}
    if kind == "mcq":
        answers = _strings(q.get("answers"))
        correct = q.get("correct")
        if not answers or len(answers) < 2 or not isinstance(correct, int) or not 0 <= correct < len(answers):
            return None
        out.update(answers=answers, correct=correct)
    elif kind == "multi":
        options = _strings(q.get("options"))
        correct = q.get("correct")
        if (not options or len(options) < 2 or not isinstance(correct, list) or not correct
                or not all(isinstance(i, int) and 0 <= i < len(options) for i in correct)):
            return None
        out.update(options=options, correct=correct)
    elif kind == "true_false":
        if not isinstance(q.get("isTrue"), bool):
            return None
        out["isTrue"] = q["isTrue"]
    elif kind == "cloze":
        answer = q.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            return None
        out["answer"] = answer
        for key in ("accept", "bank"):
            values = _strings(q.get(key))
            if values is not None:
                out[key] = values
    elif kind == "order":
        items = _strings(q.get("items"))
        if not items or len(items) < 2:
            return None
        out["items"] = items
    for key in _COMMON_QUESTION_KEYS:
        value = q.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            out[key] = value
    return _metadata(q, out)


def _card(c: dict) -> dict | None:
    front, back = c.get("front"), c.get("back")
    if not isinstance(front, str) or not isinstance(back, str) or not front.strip():
        return None
    out = {"front": front, "back": back}
    if isinstance(c.get("notion"), str):
        out["notion"] = c["notion"]
    return _metadata(c, out)


def _notions(raw, warnings: list[str]) -> list[dict] | None:
    """The checklist notions by chapter, for the notes; None when the app sent none."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise PayloadError("notions must be an array")
    if len(raw) > MAX_NOTIONS:
        raise PayloadError(f"at most {MAX_NOTIONS} notions")
    out = []
    for i, n in enumerate(raw):
        chapter = n.get("chapter") if isinstance(n, dict) else None
        quote = n.get("quote") if isinstance(n, dict) else None
        level = n.get("importance") if isinstance(n, dict) else None
        if (not isinstance(chapter, str) or not chapter.strip() or len(chapter) > 300 or not isinstance(quote, str)
                or not quote.strip() or len(quote) > 1000 or type(level) is not int or level not in IMPORTANCE_LEVELS[1:]):
            warnings.append(f"notion {i + 1} skipped: chapter, quote or importance missing")
            continue
        out.append({"chapter": chapter, "quote": quote, "importance": level})
    return out


def _review_targets(raw, warnings: list[str]) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise PayloadError("review_targets must be an object")
    questions, cards = raw.get("questions"), raw.get("cards")
    if type(questions) is not int or not 1 <= questions <= MAX_REVIEW_TARGET \
            or type(cards) is not int or not 0 <= cards <= MAX_REVIEW_TARGET:
        warnings.append(f"review_targets skipped: questions 1 to {MAX_REVIEW_TARGET}, cards 0 to {MAX_REVIEW_TARGET}")
        return None
    return {"questions": questions, "cards": cards}


def _sheet(sheet) -> dict | None:
    if sheet is None:
        return None
    if not isinstance(sheet, dict):
        raise PayloadError("sheet must be an object")
    out = {}
    for key in ("essentiel", "definitions", "structure", "pieges"):
        value = sheet.get(key)
        if isinstance(value, list):
            out[key] = value
    return out or None


def normalize(doc) -> tuple[dict, dict, list[str]]:
    """Returns (meta, content, warnings). Raises PayloadError when the document can't be used."""
    if not isinstance(doc, dict):
        raise PayloadError("expected a JSON object")
    fmt = doc.get("format")
    if fmt not in FORMATS:
        raise PayloadError(f"format must be one of {', '.join(FORMATS)}")
    warnings: list[str] = []

    visibility = doc.get("visibility", "public")
    if visibility not in VISIBILITIES:
        raise PayloadError("visibility must be public, unlisted or private")
    lang = doc.get("lang") if fmt == "flashcert-share-1" else doc.get("out_lang")
    modified = doc.get("modified_at")
    certification = _text(doc.get("certification"), 200, "certification") or None
    meta = {
        "title": _text(doc.get("title"), 300, "title", required=True),
        "certification": certification,
        "certification_uid": (_text(doc.get("certification_uid"), 100, "certification_uid") or None)
        if certification else None,
        "lang": _text(lang, 16, "lang") or None,
        "visibility": visibility,
        "position": doc["position"] if type(doc.get("position")) is int else None,
        "modified_at": modified if type(modified) is int and modified > 0 else None,
    }

    notes = _text(doc.get("notes"), 2_000_000, "notes", required=True, strip=False)

    questions_in = doc.get("questions") or []
    cards_in = doc.get("cards") or []
    if not isinstance(questions_in, list) or not isinstance(cards_in, list):
        raise PayloadError("questions and cards must be arrays")
    if len(questions_in) > MAX_QUESTIONS or len(cards_in) > MAX_CARDS:
        raise PayloadError(f"at most {MAX_QUESTIONS} questions and {MAX_CARDS} cards")

    questions, seen = [], set()
    for i, raw in enumerate(questions_in):
        q = _question(raw) if isinstance(raw, dict) else None
        if q is None:
            warnings.append(f"question {i + 1} skipped: incomplete or unknown type")
            continue
        q = {"id": _item_id(raw.get("id"), "q" + _digest(q["type"], q["q"]), seen), **q}
        questions.append(q)

    cards, seen = [], set()
    for i, raw in enumerate(cards_in):
        c = _card(raw) if isinstance(raw, dict) else None
        if c is None:
            warnings.append(f"card {i + 1} skipped: front or back missing")
            continue
        c = {"id": _item_id(raw.get("id"), "c" + _digest(c["front"], c["back"]), seen), **c}
        cards.append(c)

    content = {
        "notes": notes,
        "sheet": _sheet(doc.get("sheet")),
        "questions": questions,
        "cards": cards,
    }
    notions = _notions(doc.get("notions"), warnings)
    if notions is not None:
        content["notions"] = notions
    targets = _review_targets(doc.get("review_targets"), warnings)
    if targets is not None:
        content["review_targets"] = targets
    return meta, content, warnings


def shown(content: dict) -> dict:
    """The content as the pages get it: without the app's checklist ids."""
    def strip(items):
        return [{k: v for k, v in item.items() if k not in HIDDEN_KEYS} for item in items]
    return {**content, "questions": strip(content.get("questions", [])), "cards": strip(content.get("cards", []))}


def content_hash(meta: dict, content: dict) -> str:
    """Changes when anything a visitor sees changes (visibility aside)."""
    shown = {k: meta[k] for k in ("title", "certification", "lang", "position")}
    canonical = json.dumps([shown, content], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def confirmation_hash(meta: dict, content: dict) -> str:
    """A reviewed replacement also approves its visibility, which the ordinary content hash excludes."""
    return hashlib.sha256((content_hash(meta, content) + "\0" + meta["visibility"]).encode("utf-8")).hexdigest()


# --- Elements that visitors can flag -------------------------------------------------------------

_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*#*\s*$")


def sections(notes: str) -> dict[str, str]:
    """Section id → heading, numbered like the app: ## → "2", ### → "2.1"."""
    out: dict[str, str] = {}
    h2 = h3 = 0
    in_fence = False
    for line in notes.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        if in_fence:
            continue
        m = _HEADING.match(line)
        if not m:
            continue
        if len(m.group(1)) == 2:
            h2, h3 = h2 + 1, 0
            out[str(h2)] = m.group(2).strip()
        else:
            h2 = h2 or 1
            h3 += 1
            out[f"{h2}.{h3}"] = m.group(2).strip()
    return out


def _sheet_excerpt(sheet: dict | None, element_id: str) -> str | None:
    if not sheet:
        return None
    part, _, index = element_id.partition(".")
    items = sheet.get(part)
    if not isinstance(items, list) or not index.isdigit() or int(index) >= len(items):
        return None
    item = items[int(index)]
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("titre", "terme", "confusion"):
            if isinstance(item.get(key), str):
                return item[key]
        return json.dumps(item, ensure_ascii=False)[:300]
    return None


def excerpt(content: dict, kind: str, element_id: str, title: str) -> str | None:
    """What the flagged element says, or None when it doesn't exist in this course."""
    if kind == "course":
        return title
    if kind == "question":
        found = next((q for q in content["questions"] if q["id"] == element_id), None)
        return found["q"] if found else None
    if kind == "card":
        found = next((c for c in content["cards"] if c["id"] == element_id), None)
        return found["front"] if found else None
    if kind == "section":
        heading = sections(content["notes"]).get(element_id)
        return f"§{element_id} {heading}" if heading else None
    if kind == "sheet":
        return _sheet_excerpt(content.get("sheet"), element_id)
    return None
