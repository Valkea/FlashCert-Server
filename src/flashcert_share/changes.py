"""Human-readable change impact, computed on normalized content rather than JSON size."""
from difflib import SequenceMatcher
import re


def summarize(before: dict, after: dict, notes_limit: int = 30, items_limit: int = 20) -> dict:
    old, new = before.get("content", before), after.get("content", after)
    out = {"large": False}
    a = re.findall(r"\S+", old.get("notes") or "")
    b = re.findall(r"\S+", new.get("notes") or "")
    matched = sum(block.size for block in SequenceMatcher(None, a, b).get_matching_blocks())
    percent = round(100 * (1 - matched / max(len(a), len(b)))) if a or b else 0
    out["notes_changed_percent"] = percent
    out["large"] |= bool(a) and percent >= notes_limit
    for kind in ("questions", "cards"):
        previous = {str(x["id"]): x for x in old.get(kind, [])}
        current = {str(x["id"]): x for x in new.get(kind, [])}
        removed = len(previous.keys() - current.keys())
        added = len(current.keys() - previous.keys())
        edited = sum(previous[k] != current[k] for k in previous.keys() & current.keys())
        out[kind] = {"before": len(previous), "after": len(current), "added": added,
                     "removed": removed, "edited": edited}
        out["large"] |= bool(previous) and (removed + edited) * 100 >= len(previous) * items_limit
    sheet_changed = old.get("sheet") != new.get("sheet")
    out["sheet_changed"] = sheet_changed
    if old.get("sheet") and sheet_changed:
        # Clearing a revision sheet is destructive even when notes and item counts are unchanged.
        out["large"] |= not new.get("sheet")
    return out
