"""MongoDB subdocument key lookup (str/int key variants)."""
from __future__ import annotations


def _doc_lookup(doc: dict, key: str | int):
    """MongoDB subdocuments may use str or int keys."""
    if not isinstance(doc, dict):
        return None
    candidates: list[str | int] = [key]
    if isinstance(key, str) and key.isdigit():
        ik = int(key)
        candidates.extend([ik, key.zfill(2)])
    elif isinstance(key, int):
        candidates.extend([str(key), str(key).zfill(2)])
    seen: set[str | int] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate in doc:
            return doc[candidate]
    return None


def week_goals(goals_doc: dict, iso_year: str, iso_week: str) -> dict:
    year_bucket = _doc_lookup(goals_doc, iso_year) or {}
    result = _doc_lookup(year_bucket, iso_week)
    return result if isinstance(result, dict) else {}


def week_bucket_from_agg(agg: dict, iso_year: str, iso_week: str) -> dict:
    year_bucket = _doc_lookup(agg.get("years") or {}, iso_year) or {}
    weeks = year_bucket.get("weeks") if isinstance(year_bucket, dict) else {}
    result = _doc_lookup(weeks or {}, iso_week)
    return result if isinstance(result, dict) else {}
