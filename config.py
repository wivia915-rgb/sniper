"""
Application configuration loaded from environment variables (.env supported).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (same directory as this file).
load_dotenv(Path(__file__).resolve().parent / ".env")


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill in the values."
        )
    return value


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _optional_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _optional_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    """Runtime settings for scraper, database, and Telegram notifier."""

    telegram_bot_token: str
    telegram_chat_id: str
    search_url: str
    check_interval_seconds: int
    headless: bool
    browser_user_data_dir: str
    database_path: str
    page_load_timeout_ms: int
    scroll_passes: int
    max_alert_age_minutes: int
    time_filter_enabled: bool
    top_listings_limit: int
    sniper_mode: bool
    use_graphql: bool

    @classmethod
    def from_env(cls) -> Config:
        search_url = _optional("SEARCH_URL")
        if not search_url:
            keyword = _optional("SEARCH_KEYWORD")
            location_slug = _optional("MARKETPLACE_LOCATION_SLUG", "category/search")
            if keyword:
                from urllib.parse import quote_plus

                q = quote_plus(keyword)
                search_url = (
                    f"https://www.facebook.com/marketplace/{location_slug}/search/?query={q}"
                )
            else:
                search_url = _require("SEARCH_URL")

        from marketplace_url import ensure_newest_first_sort

        search_url = ensure_newest_first_sort(search_url)

        db_default = str(Path(__file__).resolve().parent / "data" / "seen_items.db")

        from scraper import (
            DEFAULT_CHECK_INTERVAL,
            DEFAULT_SCROLL_PASSES,
            DEFAULT_TOP_LISTINGS,
            ENABLE_TIME_FILTER,
            MAX_ALERT_AGE_MINUTES,
        )

        return cls(
            telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
            search_url=search_url,
            check_interval_seconds=_optional_int("CHECK_INTERVAL", DEFAULT_CHECK_INTERVAL),
            headless=_optional_bool("HEADLESS", True),
            browser_user_data_dir=_optional(
                "BROWSER_USER_DATA_DIR",
                str(Path(__file__).resolve().parent / "data" / "browser_profile"),
            ),
            database_path=_optional("DATABASE_PATH", db_default),
            page_load_timeout_ms=_optional_int("PAGE_LOAD_TIMEOUT_MS", 60_000),
            scroll_passes=_optional_int("SCROLL_PASSES", DEFAULT_SCROLL_PASSES),
            max_alert_age_minutes=_optional_int(
                "MAX_ALERT_AGE_MINUTES", MAX_ALERT_AGE_MINUTES
            ),
            time_filter_enabled=_optional_bool("TIME_FILTER_ENABLED", ENABLE_TIME_FILTER),
            top_listings_limit=_optional_int("TOP_LISTINGS_LIMIT", DEFAULT_TOP_LISTINGS),
            sniper_mode=_optional_bool("SNIPER_MODE", True),
            use_graphql=_optional_bool("USE_GRAPHQL", True),
        )
