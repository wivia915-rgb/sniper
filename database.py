"""
SQLite persistence for listings that have already triggered Telegram alerts.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


class SeenItemsDatabase:
    """Tracks notified marketplace listing IDs to prevent duplicate alerts."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_items (
                    listing_id TEXT PRIMARY KEY,
                    url TEXT NOT NULL UNIQUE,
                    title TEXT,
                    price TEXT,
                    location TEXT,
                    notified_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_seen_items_url ON seen_items(url)"
            )

    def has_seen(self, listing_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_items WHERE listing_id = ? LIMIT 1",
                (listing_id,),
            ).fetchone()
            return row is not None

    def mark_seen(
        self,
        listing_id: str,
        url: str,
        title: str = "",
        price: str = "",
        location: str = "",
    ) -> None:
        notified_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO seen_items
                    (listing_id, url, title, price, location, notified_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (listing_id, url, title, price, location, notified_at),
            )
