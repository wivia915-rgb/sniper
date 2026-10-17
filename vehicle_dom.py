"""Parse vehicle listings from a Playwright page (DOM)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, List
from urllib.parse import urljoin

from listing_time import clean_time_posted_text
from models import MarketplaceItem

if TYPE_CHECKING:
    from playwright.sync_api import Page

LISTING_ID_RE = re.compile(r"/marketplace/item/(\d+)")
FB_BASE = "https://www.facebook.com"

_PRICE_RE = re.compile(
    r"^("
    r"\$|€|£|FREE|Free|"
    r"MAD\s*[\d\s,]+|DH\s*[\d\s,]+|"
    r"\d{1,3}(,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
    r")$",
    re.IGNORECASE,
)
_OLD_AGE_RE = re.compile(
    r"\b(?:days?|weeks?|months?|years?|jours?|semaines?|mois|ans?)\b",
    re.IGNORECASE,
)
_HOUR_AGE_RE = re.compile(
    r"\b(?:hour|hours|heure|heures|hrs?)\b|il y a \d+\s*heure",
    re.IGNORECASE,
)
_INSTANT_LABEL_RE = re.compile(
    r"^(just listed|just now|newly listed|(?:à|a)\s*l\s*'?instant|maintenant|الآن|الان)$",
    re.IGNORECASE,
)
# Whole-string only — avoids matching listing IDs / prices embedded in blobs.
_TIME_PATTERNS = (
    re.compile(r"^just listed$", re.I),
    re.compile(r"^just now$", re.I),
    re.compile(r"^newly listed$", re.I),
    re.compile(r"^(?P<n>\d{1,3})\s*mins?\s+ago$", re.I),
    re.compile(r"^(?P<n>\d{1,3})\s*minutes?\s+ago$", re.I),
    re.compile(r"^(?P<n>\d{1,3})m$", re.I),
    re.compile(r"^(?:à|a)\s*l\s*'?instant$", re.I),
    re.compile(r"^maintenant$", re.I),
    re.compile(r"^il y a (?P<n>\d{1,3})\s*min(?:ute)?s?$", re.I),
    re.compile(r"^منذ\s+(?P<n>\d{1,3})\s*د", re.I),
    re.compile(r"^الآن$", re.I),
    re.compile(r"^الان$", re.I),
)
_STRICT_AGE_PATTERNS = tuple(p for p in _TIME_PATTERNS if p.groupindex)
_MAX_SANE_MINUTES = 180


def extract_listing_id(href: str) -> str | None:
    match = LISTING_ID_RE.search(href or "")
    return match.group(1) if match else None


def normalize_url(href: str) -> str:
    if not href:
        return ""
    path = href.split("?")[0]
    if path.startswith("http"):
        return path
    return urljoin(FB_BASE, path)


def _is_price(text: str) -> bool:
    return bool(_PRICE_RE.match((text or "").strip()))


def _is_time_text(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) > 50:
        return False
    if re.search(r"MAD|DH|\$|€|£|\d{1,3},\d{3}|Morocco|Maroc|\n", t, re.I):
        return False
    if re.fullmatch(r"\d{5,}", t):
        return False
    return any(p.fullmatch(t) for p in _TIME_PATTERNS)


def parse_vehicle_card_age_minutes(time_posted: str) -> float:
    """
    Strict card-level age in minutes for the Streamlit vehicle sniper.

    Empty / unknown → 0. Just listed → 0. Only whole-string minute patterns
    (1–3 digits) — no substring parsing on IDs or titles.
    """
    cleaned = clean_time_posted_text(time_posted)
    if not cleaned:
        return 0.0
    text = cleaned.strip()
    if _INSTANT_LABEL_RE.match(text):
        return 0.0
    for pattern in _STRICT_AGE_PATTERNS:
        match = pattern.fullmatch(text)
        if not match:
            continue
        n = int(match.group("n"))
        if n > _MAX_SANE_MINUTES:
            return 0.0
        return float(n)
    return 0.0


_CAR_LISTING_RE = re.compile(
    r"(?i)"
    r"\b("
    r"voiture|automobile|citadine|berline|break|\bsuv\b|4x4|"
    r"dacia|logan|sandero|duster|lodgy|stepway|dokker|"
    r"renault|clio|megane|symbol|kangoo|"
    r"peugeot|\b208\b|\b207\b|\b206\b|\b308\b|\b301\b|partner|rifter|"
    r"volkswagen|\bvw\b|golf|polo|passat|tiguan|"
    r"citroen|citroën|berlingo|"
    r"fiat\b|doblo|punto|palio|tipo|"
    r"mercedes|c220|e220|"
    r"\bbmw\b|"
    r"hyundai|accent|tucson|"
    r"\bkia\b|picanto|sportage|"
    r"toyota|yaris|corolla|"
    r"ford\b|focus|fiesta|"
    r"audi\b|"
    r"طوموبيل|سيارة|tomobil|tomobila"
    r")\b"
)

_MOTO_LISTING_RE = re.compile(
    r"(?i)"
    r"(moto|motocycle|scooter|scoot|\b125cc\b|\b50cc\b|\b150cc\b|\b250cc\b|"
    r"triporteur|quad|buggy|vespa|tmax|xmax|nmax|pcx|forza|\bsym\b|kymco|"
    r"\bc50\b|\bc90\b|\bc100\b|sh125|sh150|sh300|yamaha|\bhonda\b|"
    r"سكوتر|موتور|motard|cyclomoteur)"
)

_BICYCLE_RE = re.compile(
    r"(?i)(vélo|velo|bicycle|pushbike|bike\b|bici|دراجة\s*هوائية|cycle\b)"
)


def is_motorcycle_listing(item: MarketplaceItem, keywords: list[str]) -> bool:
    """True when listing looks like moto/scooter, not a car or bicycle."""
    blob = f"{item.title or ''} {item.location or ''}"
    if _CAR_LISTING_RE.search(blob):
        return False
    if _BICYCLE_RE.search(blob) and not _MOTO_LISTING_RE.search(blob):
        return False
    blob_lower = blob.lower()
    for kw in keywords:
        k = kw.strip()
        if k and (k.lower() in blob_lower or k in blob):
            return True
    return bool(_MOTO_LISTING_RE.search(blob))


def listing_time_sort_rank(time_posted: str, max_minutes: int) -> tuple[int, float]:
    """
    Sort key for scan order: verified fresh cards first (fast), then detail-needed, then old.
    Lower rank = process earlier.
    """
    cleaned = clean_time_posted_text(time_posted)
    if not cleaned:
        return (1, 0.0)
    text = cleaned.strip()
    if _OLD_AGE_RE.search(text) or _HOUR_AGE_RE.search(text):
        return (2, 999.0)
    if not card_time_needs_detail_verification(text, max_minutes):
        return (0, parse_vehicle_card_age_minutes(text))
    return (1, 0.0)


def card_time_needs_detail_verification(time_posted: str, max_minutes: int) -> bool:
    """
    True when we must open the listing page to verify age (Just listed / empty / unclear).
    False when the search card already shows minutes <= max_minutes.
    """
    cleaned = clean_time_posted_text(time_posted)
    if not cleaned:
        return True
    text = cleaned.strip()
    if _INSTANT_LABEL_RE.match(text):
        return True
    if _OLD_AGE_RE.search(text) or _HOUR_AGE_RE.search(text):
        return False
    for pattern in _STRICT_AGE_PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            n = int(match.group("n"))
            return n > int(max_minutes)
    return True


def is_vehicle_listing_fresh(time_posted: str, max_minutes: int) -> bool:
    """
    Allow: empty card time, Just listed, or verified age <= max_minutes.
    Reject: hours/days/weeks and unverified time strings.
    """
    cleaned = clean_time_posted_text(time_posted)
    if not cleaned:
        return True
    if _OLD_AGE_RE.search(cleaned):
        return False
    if _HOUR_AGE_RE.search(cleaned):
        return False
    text = cleaned.strip()
    if _INSTANT_LABEL_RE.match(text):
        return True
    for pattern in _STRICT_AGE_PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            n = int(match.group("n"))
            return n <= int(max_minutes)
    return False


def parse_listings_from_page(page: Page, limit: int) -> List[MarketplaceItem]:
    """Scrape top marketplace item links from the current DOM."""
    results: List[MarketplaceItem] = []
    seen_ids: set[str] = set()

    try:
        anchors = page.query_selector_all('a[href*="/marketplace/item/"]')
    except Exception:
        return results

    for anchor in anchors:
        try:
            href = anchor.get_attribute("href") or ""
            listing_id = extract_listing_id(href)
            if not listing_id or listing_id in seen_ids:
                continue
            seen_ids.add(listing_id)

            leaves: list[str] = anchor.evaluate(
                """(el) => {
                let card = el;
                for (let i = 0; i < 6 && card.parentElement; i++) {
                  card = card.parentElement;
                }
                const roots = [el, card];
                const out = [];
                const seen = new Set();
                for (const root of roots) {
                  root.querySelectorAll('span[dir="auto"], span, div[dir="auto"]').forEach((node) => {
                    if (node.children.length > 0) return;
                    const t = (node.textContent || '').trim();
                    if (t && !seen.has(t)) { seen.add(t); out.push(t); }
                  });
                }
                return out;
              }"""
            ) or []

            time_posted = ""
            for t in leaves:
                if _is_time_text(t):
                    time_posted = t
                    break

            price = next((t for t in leaves if _is_price(t)), "—")
            location = next(
                (
                    t
                    for t in leaves
                    if len(t) < 100
                    and "," in t
                    and not _is_price(t)
                    and not _is_time_text(t)
                ),
                "—",
            )

            title = (anchor.get_attribute("aria-label") or "").strip()
            if not title:
                title = next(
                    (
                        t
                        for t in leaves
                        if len(t) > 3
                        and not _is_price(t)
                        and not _is_time_text(t)
                        and t != location
                        and t != time_posted
                    ),
                    "Vehicle listing",
                )

            image_url = (
                anchor.evaluate(
                    """(el) => {
                    let card = el;
                    for (let i = 0; i < 6 && card.parentElement; i++) {
                      card = card.parentElement;
                    }
                    const img = el.querySelector('img') || card.querySelector('img');
                    return img ? (img.src || img.getAttribute('src') || '') : '';
                  }"""
                )
                or ""
            )

            results.append(
                MarketplaceItem(
                    listing_id=listing_id,
                    title=title,
                    price=price,
                    location=location,
                    url=normalize_url(href),
                    image_url=image_url,
                    time_posted=time_posted,
                )
            )
            if len(results) >= limit:
                break
        except Exception:
            continue

    return results
