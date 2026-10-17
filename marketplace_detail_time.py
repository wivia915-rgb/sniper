"""Sync Playwright helper: read true listing age from the product detail page."""

from __future__ import annotations

import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from listing_time import (
    clean_time_posted_text,
    contains_hour_age_indicator,
    parse_listing_age_minutes,
)

DETAIL_PAGE_TIME_SCRIPT = """
() => {
    const PROMOTIONAL = /^just\\s+listed$|^newly\\s+listed$/i;
    const TIME_HINT = /listed|listé|publié|ago|il\\s+y\\s+a|heure|minute|min\\b|منذ|دقيقة|ساعة/i;
    const EXTRACT = [
        /(listed\\s+about\\s+an?\\s+hour\\s+ago)/i,
        /(listed\\s+about\\s+\\d+\\s*(?:minutes?|mins?|hours?|hrs?)\\s+ago)/i,
        /(listed\\s+\\d+\\s*(?:minutes?|mins?|hours?|hrs?)\\s+ago)/i,
        /(listed\\s+[^\\n]{0,24}?ago)/i,
        /(about\\s+an?\\s+hour\\s+ago)/i,
        /(about\\s+\\d+\\s*(?:minutes?|mins?|hours?|hrs?)\\s+ago)/i,
        /(listé\\s+il\\s+y\\s+a\\s+(?:environ\\s+)?\\d+\\s*(?:min(?:ute)?s?|heures?))/i,
        /(publié\\s+il\\s+y\\s+a\\s+(?:environ\\s+)?\\d+\\s*(?:min(?:ute)?s?|heures?))/i,
        /(il\\s+y\\s+a\\s+(?:environ\\s+)?\\d+\\s*(?:min(?:ute)?s?|heures?))/i,
        /(\\d+\\s*(?:minutes?|mins?|hours?|hrs?)\\s+ago)/i,
    ];
    const EXACT_VERIFIED = [
        /^just\\s+now$/i,
        /^a few seconds ago$/i,
        /^\\d+\\s*seconds?\\s+ago$/i,
        /^maintenant$/i,
        /^(?:à|a)\\s*l\\s*['']?\\s*instant$/i,
        /^منذ\\s+\\d+\\s*د(?:قيقة|قائق)?$/i,
    ];

    const tryExtract = (text) => {
        const t = (text || '').replace(/\\s+/g, ' ').trim();
        if (!t || PROMOTIONAL.test(t)) return '';
        for (const re of EXTRACT) {
            const m = t.match(re);
            if (m) return m[1].trim();
        }
        if (EXACT_VERIFIED.some((re) => re.test(t))) return t;
        return '';
    };

    const candidates = [];
    const pushCandidate = (text) => {
        const t = (text || '').replace(/\\s+/g, ' ').trim();
        if (!t || t.length > 220) return;
        if (!TIME_HINT.test(t)) return;
        if (/^(FREE|MAD|\\$|€|£)$/i.test(t)) return;
        candidates.push(t);
    };

    document.querySelectorAll('span[dir="auto"], div[dir="auto"], span, div').forEach((el) => {
        if (el.children.length > 0) return;
        pushCandidate(el.textContent || '');
    });
    document.querySelectorAll('[dir="auto"], span, div, a').forEach((el) => {
        const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        if (t.length > 120 || t.length < 4) return;
        if (!TIME_HINT.test(t)) return;
        pushCandidate(t);
    });

    for (const t of candidates) {
        const hit = tryExtract(t);
        if (hit) return hit;
    }

    const body = (document.body?.innerText || '').slice(0, 25000);
    for (const re of EXTRACT) {
        const m = body.match(re);
        if (m) return m[1].trim();
    }
    return '';
}
"""


_NETWORK_MARKERS = (
    "ERR_NAME_NOT_RESOLVED",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_NETWORK_CHANGED",
    "ERR_CONNECTION_RESET",
)


def _goto_with_retry(page, url: str, *, retries: int = 3) -> None:
    last: BaseException | None = None
    for attempt in range(1, retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            return
        except (PlaywrightError, PlaywrightTimeout) as exc:
            last = exc
            msg = str(exc)
            if not any(m in msg for m in _NETWORK_MARKERS) or attempt >= retries:
                raise
            time.sleep(2 * attempt)
    if last:
        raise last


def _extract_time_from_body_text(body: str) -> str:
    """Fallback when DOM script misses FB layout variants."""
    from listing_time import extract_time_phrase

    phrase = extract_time_phrase(body[:25000] if body else "")
    return clean_time_posted_text(phrase) if phrase else ""


def fetch_detail_time_posted(page, listing_url: str) -> str:
    """Navigate to listing detail and return cleaned relative time text."""
    _goto_with_retry(page, listing_url)
    page.wait_for_timeout(1600)
    try:
        page.wait_for_function(
            """
            () => {
                const t = document.body?.innerText || '';
                return /listed|ago|il y a|min|heure|دقيقة|منذ/i.test(t);
            }
            """,
            timeout=12_000,
        )
    except Exception:
        pass
    raw = page.evaluate(DETAIL_PAGE_TIME_SCRIPT) or ""
    cleaned = clean_time_posted_text(str(raw).strip())
    if cleaned:
        return cleaned
    try:
        body = page.locator("body").inner_text(timeout=5_000)
    except Exception:
        body = ""
    return _extract_time_from_body_text(body)


def is_verified_detail_time_within_minutes(time_posted: str, max_minutes: int) -> bool:
    """
    Strict gate using detail-page text (never trust search-card Just listed).
    """
    from vehicle_dom import _INSTANT_LABEL_RE, _OLD_AGE_RE  # shared patterns

    cleaned = clean_time_posted_text(time_posted)
    if not cleaned:
        return False
    if _OLD_AGE_RE.search(cleaned) or contains_hour_age_indicator(cleaned):
        return False
    if _INSTANT_LABEL_RE.match(cleaned.strip()):
        return True
    age = parse_listing_age_minutes(cleaned)
    if age is None:
        return False
    return age <= float(max_minutes)


def listing_passes_time_gate(
    detail_time: str,
    card_time: str,
    max_minutes: int,
) -> bool:
    """
    Detail page is authoritative when text is found.
    If detail extraction is empty, trust the search card only when it shows
    strict minutes (not « Just listed » / empty).
    """
    from vehicle_dom import (
        card_time_needs_detail_verification,
        is_vehicle_listing_fresh,
    )

    if is_verified_detail_time_within_minutes(detail_time, max_minutes):
        return True
    if (detail_time or "").strip():
        return False
    if not card_time_needs_detail_verification(card_time, max_minutes):
        return is_vehicle_listing_fresh(card_time, max_minutes)
    return False


def should_mark_seen_after_time_reject(
    detail_time: str, card_time: str, max_minutes: int
) -> bool:
    """Retry next cycle when detail time could not be read (empty extraction)."""
    if (detail_time or "").strip():
        return True
    from vehicle_dom import card_time_needs_detail_verification

    if not card_time_needs_detail_verification(card_time, max_minutes):
        return True
    return False
