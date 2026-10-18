"""Human-readable change impact, computed on normalized content rather than JSON size."""
from difflib import SequenceMatcher
import json
import re

from .payload import METADATA_KEYS

# Lists that mean the same empty or missing: older servers filled them in.
_OPTIONAL_LISTS = ("sources", "accept", "bank")


def changed_percent(before: str, after: str) -> int:
    a, b = re.findall(r"\S+", before), re.findall(r"\S+", after)
    matched = sum(block.size for block in SequenceMatcher(None, a, b).get_matching_blocks())
    return round(100 * (1 - matched / max(len(a), len(b)))) if a or b else 0


def _comparable(item: dict) -> dict:
    """What an edit can change: neither checklist metadata, nor empty optional lists, nor the order of right options."""
    out = {k: v for k, v in item.items() if k not in METADATA_KEYS and not (k in _OPTIONAL_LISTS and v == [])}
    if isinstance(out.get("correct"), list):
        out["correct"] = sorted(set(out["correct"]))
    return out


def summarize(before: dict, after: dict, notes_limit: int = 30, items_limit: int = 20) -> dict:
    old, new = before.get("content", before), after.get("content", after)
    out = {"large": False}
    percent = changed_percent(old.get("notes") or "", new.get("notes") or "")
    out["notes_changed_percent"] = percent
    out["large"] |= bool(old.get("notes")) and percent >= notes_limit
    for kind in ("questions", "cards"):
        previous = {str(x["id"]): x for x in old.get(kind, [])}
        current = {str(x["id"]): x for x in new.get(kind, [])}
        removed = len(previous.keys() - current.keys())
        added = len(current.keys() - previous.keys())
        edited = sum(_comparable(previous[k]) != _comparable(current[k]) for k in previous.keys() & current.keys())
        out[kind] = {"before": len(previous), "after": len(current), "added": added,
                     "removed": removed, "edited": edited}
        out["large"] |= bool(previous) and (removed + edited) * 100 >= len(previous) * items_limit
    sheet_changed = old.get("sheet") != new.get("sheet")
    out["sheet_changed"] = sheet_changed
    sheet_percent = changed_percent(json.dumps(old.get("sheet"), sort_keys=True, ensure_ascii=False),
                                    json.dumps(new.get("sheet"), sort_keys=True, ensure_ascii=False)) if sheet_changed else 0
    out["sheet_changed_percent"] = sheet_percent
    if old.get("sheet") and sheet_changed:
        out["large"] |= not new.get("sheet") or sheet_percent >= notes_limit
    return out
