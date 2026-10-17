"""Load and save Streamlit sniper settings (sniper_config.json)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import quote_plus

CONFIG_PATH = Path(__file__).resolve().parent / "sniper_config.json"

CITY_OPTIONS = (
    "Casablanca",
    "Rabat",
    "Marrakech",
    "Fez",
    "Tangier",
    "All Cities",
)

CITY_SLUGS: dict[str, str] = {
    "Casablanca": "casablanca",
    "Rabat": "rabat",
    "Marrakech": "marrakech",
    "Fez": "fes",
    "Tangier": "tanger",
    "All Cities": "",
}

# Moto / scooter only — no voiture keywords.
VEHICLE_KEYWORDS: tuple[str, ...] = (
    "moto",
    "motocycle",
    "scooter",
    "triporteur",
    "quad",
    "buggy",
    "سكوتر",
    "طريبورتور",
    "50cc",
    "125cc",
    "150cc",
    "250cc",
    "c50",
    "c90",
    "c100",
    "sh125",
    "sh150",
    "sh300",
    "tmax",
    "xmax",
    "nmax",
    "pcx",
    "forza",
    "vespa",
    "beverly",
    "docker",
    "sanya",
    "sym",
    "kymco",
    "سانيا",
    "دوكير",
    "دكير",
    "سي 90",
    "سيم",
    "تيماكس",
    "اكماكس",
    "بي سي اكس",
)

DEFAULT_SEARCH_KEYWORDS = VEHICLE_KEYWORDS

# Short list for faster full rotation (turbo preset).
TURBO_SEARCH_KEYWORDS: tuple[str, ...] = (
    "moto",
    "scooter",
    "motocycle",
    "triporteur",
    "125cc",
    "50cc",
    "tmax",
    "pcx",
    "sym",
    "nmax",
    "xmax",
    "سكوتر",
)


class SniperSettings(TypedDict):
    facebook_email: str
    facebook_password: str
    telegram_bot_token: str
    telegram_chat_id: str
    turbo_mode: bool
    city: str
    search_keywords: list[str]
    time_filter_enabled: bool
    max_alert_age_minutes: int
    check_interval_seconds: int
    top_listings_limit: int
    headless: bool


DEFAULTS: SniperSettings = {
    "facebook_email": "",
    "facebook_password": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "turbo_mode": False,
    "city": "Casablanca",
    "search_keywords": list(DEFAULT_SEARCH_KEYWORDS),
    "time_filter_enabled": True,
    "max_alert_age_minutes": 7,
    "check_interval_seconds": 15,
    "top_listings_limit": 20,
    "headless": True,
}


def parse_search_keywords(raw: Any) -> list[str]:
    """Parse keywords from JSON list or comma/newline-separated string."""
    if raw is None:
        return list(DEFAULT_SEARCH_KEYWORDS)
    if isinstance(raw, list):
        out = [str(k).strip() for k in raw if str(k).strip()]
        return out or list(DEFAULT_SEARCH_KEYWORDS)
    if isinstance(raw, str):
        parts = re.split(r"[,;\n]+", raw)
        out = [p.strip() for p in parts if p.strip()]
        return out or list(DEFAULT_SEARCH_KEYWORDS)
    return list(DEFAULT_SEARCH_KEYWORDS)


def load_config(path: Path | None = None) -> SniperSettings:
    path = path or CONFIG_PATH
    if not path.is_file():
        return dict(DEFAULTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULTS)
    merged: dict[str, Any] = dict(DEFAULTS)
    if isinstance(raw, dict):
        merged.update(raw)
    city = str(merged.get("city", DEFAULTS["city"]))
    if city not in CITY_OPTIONS:
        city = DEFAULTS["city"]
    return SniperSettings(
        facebook_email=str(merged.get("facebook_email", "")),
        facebook_password=str(merged.get("facebook_password", "")),
        telegram_bot_token=str(merged.get("telegram_bot_token", "")),
        telegram_chat_id=str(merged.get("telegram_chat_id", "")),
        turbo_mode=bool(merged.get("turbo_mode", False)),
        city=city,
        search_keywords=parse_search_keywords(merged.get("search_keywords")),
        time_filter_enabled=bool(merged.get("time_filter_enabled", True)),
        max_alert_age_minutes=int(merged.get("max_alert_age_minutes", 7)),
        check_interval_seconds=max(10, int(merged.get("check_interval_seconds", 15))),
        top_listings_limit=max(5, min(50, int(merged.get("top_listings_limit", 20)))),
        headless=bool(merged.get("headless", True)),
    )


def save_config(settings: SniperSettings, path: Path | None = None) -> None:
    path = path or CONFIG_PATH
    path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def marketplace_moto_url(city_label: str, keyword: str | None = None) -> str:
    """Marketplace moto search (not /vehicles/ — avoids car listings)."""
    slug = CITY_SLUGS.get(city_label, CITY_SLUGS["Casablanca"])
    q = (keyword or "").strip()
    if q:
        if city_label == "All Cities" or not slug:
            return (
                "https://www.facebook.com/marketplace/search/"
                f"?query={quote_plus(q)}&sortBy=creation_time_descend"
            )
        return (
            f"https://www.facebook.com/marketplace/{slug}/search/"
            f"?query={quote_plus(q)}&sortBy=creation_time_descend"
        )
    if city_label == "All Cities" or not slug:
        base = "https://www.facebook.com/marketplace/category/motorcycles/"
    else:
        base = f"https://www.facebook.com/marketplace/{slug}/motorcycles/"
    return f"{base}?sortBy=creation_time_descend"


def marketplace_vehicles_url(city_label: str, keyword: str | None = None) -> str:
    """Alias — moto-only sniper uses marketplace_moto_url."""
    return marketplace_moto_url(city_label, keyword)


# Scanned every cycle (after category) — catch fresh motos before long-tail keywords.
PRIORITY_SEARCH_KEYWORDS: tuple[str, ...] = (
    "moto",
    "scooter",
    "motocycle",
    "triporteur",
    "50cc",
    "125cc",
    "سكوتر",
    "طريبورتور",
)


def _keyword_order(city_label: str, keywords: list[str]) -> list[str]:
    """Category-first, then priority terms, then the rest (stable, deduped)."""
    kws = [k.strip() for k in keywords if k.strip()]
    if not kws:
        return []
    lower_to_original: dict[str, str] = {}
    for k in kws:
        lower_to_original.setdefault(k.lower(), k)
    ordered: list[str] = []
    seen_lower: set[str] = set()

    def add_kw(raw: str) -> None:
        key = raw.strip().lower()
        if not key or key in seen_lower or key not in lower_to_original:
            return
        seen_lower.add(key)
        ordered.append(lower_to_original[key])

    for pk in PRIORITY_SEARCH_KEYWORDS:
        add_kw(pk)
    for k in kws:
        add_kw(k)
    return ordered


def marketplace_search_urls(city_label: str, keywords: list[str]) -> list[str]:
    """
    Feed list: motorcycles category (newest sort) + one search URL per keyword.
    Priority keywords come right after the category feed for faster fresh catches.
    """
    ordered = _keyword_order(city_label, keywords)
    if not ordered:
        return [marketplace_moto_url(city_label)]
    urls = [marketplace_moto_url(city_label)]
    urls.extend(marketplace_moto_url(city_label, kw) for kw in ordered)
    return urls
