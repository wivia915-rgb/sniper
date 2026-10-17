"""Persistent deduplication store for listing IDs (seen_listings.json)."""

from __future__ import annotations

import json
from pathlib import Path

SEEN_PATH = Path(__file__).resolve().parent / "seen_listings.json"
MAX_SEEN = 2000


def load_seen(path: Path | None = None) -> set[str]:
    path = path or SEEN_PATH
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    if isinstance(data, list):
        return set(str(x) for x in data)
    if isinstance(data, dict) and isinstance(data.get("ids"), list):
        return set(str(x) for x in data["ids"])
    return set()


def save_seen(seen: set[str], path: Path | None = None) -> None:
    path = path or SEEN_PATH
    ids = list(seen)[-MAX_SEEN:]
    path.write_text(
        json.dumps({"ids": ids}, indent=2) + "\n",
        encoding="utf-8",
    )


def clear_seen(path: Path | None = None) -> None:
    path = path or SEEN_PATH
    if path.is_file():
        path.unlink(missing_ok=True)


def seen_count(path: Path | None = None) -> int:
    return len(load_seen(path))


def mark_seen(listing_id: str, seen: set[str], path: Path | None = None) -> None:
    seen.add(listing_id)
    if len(seen) > MAX_SEEN:
        trimmed = set(list(seen)[-MAX_SEEN:])
        seen.clear()
        seen.update(trimmed)
    save_seen(seen, path)
