"""
Parse Facebook Marketplace relative time strings (EN / FR / AR).
"""

from __future__ import annotations

import re
from typing import Optional

# --- English ---
_EN_LISTED_MINUTES_AGO_RE = re.compile(
    r"listed\s+(?P<n>\d+)\s*minutes?\s+ago",
    re.IGNORECASE,
)
_EN_LISTED_MINS_AGO_RE = re.compile(
    r"listed\s+(?P<n>\d+)\s*mins?\s+ago",
    re.IGNORECASE,
)
_EN_LISTED_HOURS_AGO_RE = re.compile(
    r"listed\s+(?P<n>\d+)\s*hours?\s+ago",
    re.IGNORECASE,
)
_EN_HOURS_AGO_RE = re.compile(
    r"(?P<n>\d+)\s*hours?\s+ago\b",
    re.IGNORECASE,
)
_HOUR_INDICATOR_RE = re.compile(
    r"(?:"
    r"\babout\s+an?\s+hours?\s+ago\b|"
    r"\blisted\s+about\s+an?\s+hours?\s+ago\b|"
    r"\b\d+\s*(?:hour|hours|hr|hrs|h)\s+ago\b|"
    r"\blisted\s+\d+\s*hours?\s+ago\b|"
    r"\bil\s+y\s+a\s+(?:environ\s+)?(?:une|\d+)\s*heures?\b|"
    r"\b\d+\s*heures?\s+ago\b|"
    r"منذ\s+[\d٠-٩۰-۹]+\s*س|"
    r"منذ\s+س(?:اعة|اعة)"
    r")",
    re.IGNORECASE,
)
_EN_LISTED_ABOUT_HOUR_AGO_RE = re.compile(
    r"listed\s+about\s+an?\s+hour\s+ago",
    re.IGNORECASE,
)
_EN_ABOUT_HOUR_AGO_RE = re.compile(
    r"about\s+an?\s+hour\s+ago",
    re.IGNORECASE,
)
_EN_LISTED_ABOUT_MINUTES_AGO_RE = re.compile(
    r"listed\s+about\s+(?P<n>\d+)\s*(?:minutes?|mins?)\s+ago",
    re.IGNORECASE,
)
_EN_ABOUT_MINUTES_AGO_RE = re.compile(
    r"about\s+(?P<n>\d+)\s*(?:minutes?|mins?)\s+ago",
    re.IGNORECASE,
)
_EN_LISTED_ABOUT_HOURS_NUM_AGO_RE = re.compile(
    r"listed\s+about\s+(?P<n>\d+)\s*hours?\s+ago",
    re.IGNORECASE,
)
_TIME_PHRASE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"listed\s+about\s+an?\s+hour\s+ago", re.I),
    re.compile(r"listed\s+about\s+\d+\s*(?:minutes?|mins?|hours?|hrs?)\s+ago", re.I),
    re.compile(r"listed\s+\d+\s*(?:minutes?|mins?|hours?|hrs?)\s+ago", re.I),
    re.compile(r"about\s+an?\s+hour\s+ago", re.I),
    re.compile(r"about\s+\d+\s*(?:minutes?|mins?|hours?|hrs?)\s+ago", re.I),
    re.compile(r"\d+\s*(?:minutes?|mins?|hours?|hrs?)\s+ago", re.I),
    re.compile(r"il\s+y\s+a\s+(?:environ\s+)?\d+\s*min(?:ute)?s?", re.I),
    re.compile(r"il\s+y\s+a\s+(?:environ\s+)?(?:une|\d+)\s*heures?", re.I),
    re.compile(r"listé\s+il\s+y\s+a\s+\d+\s*min(?:ute)?s?", re.I),
    re.compile(r"منذ\s+[\d٠-٩۰-۹]+\s*د(?:قيقة|قائق)?", re.I),
    re.compile(r"just\s+now|just\s+listed", re.I),
)
_LOCATION_SUFFIX_RE = re.compile(
    r"\s+(?:in|à|en|au|aux|في)\s+.+$",
    re.IGNORECASE,
)
_PROMOTIONAL_UNVERIFIED_RE = re.compile(
    r"^(?:"
    r"just\s+listed|newly\s+listed|"
    r"vient\s+d['']?\s*(?:être\s+)?(?:publié|publie|listé|liste|annoncé|annonce)|"
    r"أضيف\s+للتو|اضيف\s+للتو|تم\s+النشر\s+للتو|منشور\s+الآن"
    r")$",
    re.IGNORECASE,
)
_EN_MINUTE_RE = re.compile(
    r"(?P<n>\d+)\s*(?:m|min|mins|minute|minutes)\b",
    re.IGNORECASE,
)
_EN_MINUTES_AGO_RE = re.compile(
    r"(?P<n>\d+)\s*mins?\s+ago\b",
    re.IGNORECASE,
)
_EN_MINUTES_AGO_LONG_RE = re.compile(
    r"(?P<n>\d+)\s*minutes?\s+ago\b",
    re.IGNORECASE,
)
_EN_SECOND_RE = re.compile(
    r"(?P<n>\d+)\s*(?:s|sec|secs|second|seconds)\b",
    re.IGNORECASE,
)
_EN_HOUR_RE = re.compile(
    r"(?P<n>\d+)\s*(?:h|hr|hrs|hour|hours)\b",
    re.IGNORECASE,
)
_EN_DAY_RE = re.compile(
    r"(?P<n>\d+)\s*(?:d|day|days)\b",
    re.IGNORECASE,
)
_EN_COMPACT_MINUTE_RE = re.compile(
    r"(?P<n>\d+)m\b",
    re.IGNORECASE,
)
_EN_COMPACT_MINUTE_ONLY_RE = re.compile(
    r"^(?P<n>\d+)m$",
    re.IGNORECASE,
)
_EN_COMPACT_HOUR_RE = re.compile(r"(?P<n>\d+)h\b", re.IGNORECASE)
_EN_WEEK_OR_LONGER_RE = re.compile(
    r"\b(?:week|weeks|month|months|year|years)\b",
    re.IGNORECASE,
)
_EN_JUST_LISTED_RE = re.compile(
    r"\bjust\s+listed\b|\bnewly\s+listed\b|\blisted\s+just\s+now\b",
    re.IGNORECASE,
)

# --- French ---
_FR_JUST_NOW_RE = re.compile(
    r"(?:à|a)\s*l\s*['']?\s*instant|maintenant|\binstant\b",
    re.IGNORECASE,
)
_FR_MINUTE_RE = re.compile(
    r"(?:il\s+y\s+a\s+)?(?P<n>\d+)\s*(?:min(?:ute)?s?|m)\.?(?:\s|$|·)",
    re.IGNORECASE,
)
_FR_LISTED_MINUTE_RE = re.compile(
    r"(?:listé|publié|annoncé)\s+il\s+y\s+a\s+(?P<n>\d+)\s*min(?:ute)?s?",
    re.IGNORECASE,
)
_FR_ONE_MINUTE_RE = re.compile(
    r"il\s+y\s+a\s+(?:une|1)\s+minutes?\b",
    re.IGNORECASE,
)
_FR_SECOND_RE = re.compile(
    r"il\s+y\s+a\s+(?P<n>\d+)\s*(?:s|sec|seconde|secondes)\b",
    re.IGNORECASE,
)
_FR_FEW_SECONDS_RE = re.compile(
    r"il\s+y\s+a\s+quelques\s+secondes\b",
    re.IGNORECASE,
)
_FR_HOUR_RE = re.compile(
    r"(?:il\s+y\s+a\s+)?(?P<n>\d+)\s*(?:h|heure|heures)\b",
    re.IGNORECASE,
)
_FR_ONE_HOUR_RE = re.compile(r"il\s+y\s+a\s+(?:une|1)\s+heure\b", re.IGNORECASE)
_FR_DAY_RE = re.compile(
    r"(?:il\s+y\s+a\s+)?(?P<n>\d+)\s*(?:j|jour|jours)\b",
    re.IGNORECASE,
)
_FR_JUST_LISTED_RE = re.compile(
    r"vient\s+d['']?\s*(?:être\s+)?(?:publié|publie|listé|liste|annoncé|annonce)",
    re.IGNORECASE,
)

# --- Arabic ---
_AR_NOW_RE = re.compile(r"الآن|الان|حال(?:اً|ا)|منذ\s+لحظة")
_AR_MINUTE_RE = re.compile(
    r"منذ\s*(?P<n>[\d٠-٩۰-۹]+)\s*(?:د(?:قيقة|قائق)?|د\.?)(?:\s|$|،|,)",
)
_AR_ONE_MINUTE_RE = re.compile(
    r"منذ\s*(?:دقيقة\s*واحدة?|د\s*واحدة?)|(?:دقيقة\s*واحدة?)",
)
_AR_SECOND_RE = re.compile(
    r"منذ\s*(?P<n>[\d٠-٩۰-۹]+)\s*(?:ث(?:انية|ان|انی)?|ث\.?)",
)
_AR_HOUR_RE = re.compile(
    r"منذ\s*(?P<n>[\d٠-٩۰-۹]+)\s*(?:س(?:اعة|اعات|)?|س\.?)",
)
_AR_DAY_RE = re.compile(
    r"منذ\s*(?P<n>[\d٠-٩۰-۹]+)\s*(?:ي(?:وم|وم)?|ي\.?)",
)
_AR_JUST_LISTED_RE = re.compile(
    r"أضيف\s+للتو|اضيف\s+للتو|تم\s+النشر\s+للتو|منشور\s+الآن",
)

DEFAULT_MAX_ALERT_AGE_MINUTES = 10

_JUST_LISTED_EXACT_RE = re.compile(r"^just\s+listed$", re.IGNORECASE)

# Pull a relative-time phrase out of noisy card/detail blobs.
_EXTRACT_TIME_PHRASE_RE = re.compile(
    r"(?:"
    r"listed\s+\d+\s*(?:minutes?|mins?)\s+ago|"
    r"listé\s+il\s+y\s+a\s+\d+\s*min(?:ute)?s?|"
    r"publié\s+il\s+y\s+a\s+\d+\s*min(?:ute)?s?|"
    r"il\s+y\s+a\s+\d+\s*min(?:ute)?s?|"
    r"\d+\s*minutes?\s+ago|\d+\s*mins?\s+ago|"
    r"just\s+listed|just\s+now|"
    r"(?:à|a)\s*l\s*'?instant|"
    r"maintenant|"
    r"منذ\s+\d+\s*د(?:قيقة|قائق)?|"
    r"الآن|الان"
    r")",
    re.IGNORECASE,
)

_DIGIT_MAP = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)


def _normalize_digits(text: str) -> str:
    return text.translate(_DIGIT_MAP)


def _parse_int(raw: str) -> int:
    return int(_normalize_digits(raw))


def _normalize_time_text(time_posted: str) -> str:
    return re.sub(r"\s+", " ", (time_posted or "").strip())


def extract_time_phrase(time_posted: str) -> str:
    """
    Pull only the relative-time portion (e.g. strip ' in Casablanca, Morocco').
    """
    text = _normalize_time_text(time_posted)
    if not text:
        return ""
    for pattern in _TIME_PHRASE_PATTERNS:
        match = pattern.search(text)
        if match:
            return _normalize_time_text(match.group(0))
    stripped = _LOCATION_SUFFIX_RE.sub("", text).strip()
    if stripped != text:
        for pattern in _TIME_PHRASE_PATTERNS:
            match = pattern.search(stripped)
            if match:
                return _normalize_time_text(match.group(0))
        return stripped
    return text


def clean_time_posted_text(time_posted: str) -> str:
    """
    Normalize whitespace and extract the relative-time phrase from a raw DOM string.
    """
    text = _normalize_time_text(time_posted)
    if not text:
        return ""
    phrase = extract_time_phrase(text)
    if phrase and len(phrase) <= 80:
        return phrase
    if len(text) <= 50 and not re.search(
        r"MAD|DH|\$|€|£|\d{1,3},\d{3}|Morocco|Maroc|\n", text, re.IGNORECASE
    ):
        return text
    match = _EXTRACT_TIME_PHRASE_RE.search(text)
    if match:
        return _normalize_time_text(match.group(0))
    return text


def is_just_listed(time_posted: str) -> bool:
    """True when the text is exactly 'Just listed' (case-insensitive)."""
    normalized = clean_time_posted_text(time_posted)
    if not normalized:
        return False
    return _JUST_LISTED_EXACT_RE.match(normalized) is not None


def is_promotional_unverified_label(time_posted: str) -> bool:
    """
    True for FB promotional labels that do not prove the listing is fresh
    (e.g. search-card 'Just listed' on hour-old items).
    """
    normalized = clean_time_posted_text(time_posted)
    if not normalized:
        return False
    if _PROMOTIONAL_UNVERIFIED_RE.match(normalized):
        return True
    if is_just_listed(normalized):
        return True
    return False


def contains_hour_age_indicator(time_posted: str) -> bool:
    """True when text explicitly states an age in hours (always > 10 min)."""
    normalized = clean_time_posted_text(time_posted)
    if not normalized:
        return False
    return _HOUR_INDICATOR_RE.search(normalized) is not None


def parse_listing_age_minutes(time_posted: str) -> Optional[float]:
    """
    Convert Marketplace time-posted text to age in minutes.

    Supports English, French, and common Arabic Marketplace strings.
    Returns None if the text is empty or cannot be parsed.
    """
    raw = clean_time_posted_text(time_posted)
    if not raw:
        return None

    text = _normalize_digits(raw)
    lower = text.lower()

    # Product page: "Listed 56 minutes ago"
    if _EN_LISTED_ABOUT_HOUR_AGO_RE.search(lower) or _EN_ABOUT_HOUR_AGO_RE.search(lower):
        return 60.0

    listed_about_min = _EN_LISTED_ABOUT_MINUTES_AGO_RE.search(lower)
    if listed_about_min:
        return float(_parse_int(listed_about_min.group("n")))

    about_min = _EN_ABOUT_MINUTES_AGO_RE.search(lower)
    if about_min:
        return float(_parse_int(about_min.group("n")))

    listed_about_h = _EN_LISTED_ABOUT_HOURS_NUM_AGO_RE.search(lower)
    if listed_about_h:
        return float(_parse_int(listed_about_h.group("n"))) * 60.0

    listed_long = _EN_LISTED_MINUTES_AGO_RE.search(lower)
    if listed_long:
        return float(_parse_int(listed_long.group("n")))
    listed_short = _EN_LISTED_MINS_AGO_RE.search(lower)
    if listed_short:
        return float(_parse_int(listed_short.group("n")))

    fr_listed = _FR_LISTED_MINUTE_RE.search(lower)
    if fr_listed:
        return float(_parse_int(fr_listed.group("n")))

    listed_hours = _EN_LISTED_HOURS_AGO_RE.search(lower)
    if listed_hours:
        return float(_parse_int(listed_hours.group("n"))) * 60.0

    hours_ago = _EN_HOURS_AGO_RE.search(lower)
    if hours_ago:
        return float(_parse_int(hours_ago.group("n"))) * 60.0

    # Promotional labels are not a verifiable age (handled in is_within_time_limit).
    if is_promotional_unverified_label(raw):
        return None

    # --- immediate (numeric seconds / explicit 'just now' on detail pages) ---
    if re.search(r"\bjust now\b", lower):
        return 0.0
    if _FR_JUST_NOW_RE.search(raw) or _FR_JUST_NOW_RE.search(lower):
        return 0.0
    if _AR_NOW_RE.search(raw):
        return 0.0
    if re.search(r"\ba few seconds ago\b", lower):
        return 0.0
    if re.search(r"\b(?:a|few)\s+seconds?\b", lower):
        return 0.0
    if re.search(r"\bquelques\s+secondes\b", lower):
        return 0.0
    if _FR_FEW_SECONDS_RE.search(lower):
        return 0.0

    if _EN_WEEK_OR_LONGER_RE.search(lower):
        return None

    sec_match = _EN_SECOND_RE.search(lower)
    if sec_match:
        return _parse_int(sec_match.group("n")) / 60.0

    fr_sec = _FR_SECOND_RE.search(lower)
    if fr_sec:
        return _parse_int(fr_sec.group("n")) / 60.0

    ar_sec = _AR_SECOND_RE.search(text)
    if ar_sec:
        return _parse_int(ar_sec.group("n")) / 60.0

    ago_long = _EN_MINUTES_AGO_LONG_RE.search(lower)
    if ago_long:
        return float(_parse_int(ago_long.group("n")))

    ago_short = _EN_MINUTES_AGO_RE.search(lower)
    if ago_short:
        return float(_parse_int(ago_short.group("n")))

    minute_match = _EN_MINUTE_RE.search(lower)
    if minute_match:
        return float(_parse_int(minute_match.group("n")))

    compact_min = _EN_COMPACT_MINUTE_RE.search(lower)
    if compact_min:
        return float(_parse_int(compact_min.group("n")))

    compact_only = _EN_COMPACT_MINUTE_ONLY_RE.search(lower)
    if compact_only:
        return float(_parse_int(compact_only.group("n")))

    if re.search(r"\b(?:a|an|one)\s+minutes?\b", lower):
        return 1.0

    if _FR_ONE_MINUTE_RE.search(lower):
        return 1.0

    fr_min = _FR_MINUTE_RE.search(lower)
    if fr_min:
        return float(_parse_int(fr_min.group("n")))

    if _AR_ONE_MINUTE_RE.search(text):
        return 1.0

    ar_min = _AR_MINUTE_RE.search(text)
    if ar_min:
        return float(_parse_int(ar_min.group("n")))

    if re.search(r"\b(?:about\s+)?(?:an|a|one)\s+hours?\b", lower):
        return 60.0

    if _FR_ONE_HOUR_RE.search(lower):
        return 60.0

    hour_match = _EN_HOUR_RE.search(lower)
    if hour_match:
        return float(_parse_int(hour_match.group("n"))) * 60.0

    compact_hour = _EN_COMPACT_HOUR_RE.search(lower)
    if compact_hour:
        return float(_parse_int(compact_hour.group("n"))) * 60.0

    fr_hour = _FR_HOUR_RE.search(lower)
    if fr_hour:
        return float(_parse_int(fr_hour.group("n"))) * 60.0

    ar_hour = _AR_HOUR_RE.search(text)
    if ar_hour:
        return float(_parse_int(ar_hour.group("n"))) * 60.0

    if re.search(r"\byesterday\b", lower) or re.search(r"\bhier\b", lower):
        return 24.0 * 60.0

    day_match = _EN_DAY_RE.search(lower)
    if day_match:
        return float(_parse_int(day_match.group("n"))) * 24.0 * 60.0

    fr_day = _FR_DAY_RE.search(lower)
    if fr_day:
        return float(_parse_int(fr_day.group("n"))) * 24.0 * 60.0

    ar_day = _AR_DAY_RE.search(text)
    if ar_day:
        return float(_parse_int(ar_day.group("n"))) * 24.0 * 60.0

    return None


def is_within_time_limit(
    time_posted: str,
    max_minutes: int = DEFAULT_MAX_ALERT_AGE_MINUTES,
    *,
    sniper_mode: bool = False,
) -> bool:
    """
    True only when age is numerically verified and <= max_minutes.

    Promotional tags, hour-based ages, and unparseable text are rejected.
    """
    raw_time = (time_posted or "").strip()
    if sniper_mode and not raw_time:
        print(
            "[time_parse] raw='' allowed=True reason=sniper_mode_empty_bypass",
            flush=True,
        )
        return True

    cleaned = clean_time_posted_text(time_posted)
    reason = "ok"

    if not cleaned:
        reason = "empty_unverified"
        allowed = False
        parsed_display = "?"
    elif is_promotional_unverified_label(cleaned):
        reason = "promotional_unverified"
        allowed = False
        parsed_display = "?"
    elif contains_hour_age_indicator(cleaned):
        reason = "hours_detected"
        age = parse_listing_age_minutes(cleaned)
        parsed_display = "?" if age is None else round(age, 2)
        allowed = False
    else:
        age = parse_listing_age_minutes(cleaned)
        if age is None:
            reason = "unparseable_unverified"
            allowed = False
        elif age > max_minutes:
            reason = "over_max_age"
            allowed = False
        else:
            allowed = True
        parsed_display = "?" if age is None else round(age, 2)

    print(
        f"[time_parse] raw={cleaned!r} parsed_minutes={parsed_display} "
        f"allowed={allowed} reason={reason}",
        flush=True,
    )
    return allowed


def is_listing_within_minutes(
    time_posted: str,
    max_minutes: int = DEFAULT_MAX_ALERT_AGE_MINUTES,
) -> bool:
    """Alias for is_within_time_limit (backward compatible)."""
    return is_within_time_limit(time_posted, max_minutes)


SNIPER_FALLBACK_TIME_LABEL = "Just now"


def sniper_apply_empty_time_fallback(
    time_posted: str,
    *,
    listing_id: str = "",
    feed_rank: int = 0,
) -> str:
    """
    SNIPER_MODE: feed is sortBy=creation_time_descend — missing card time => treat as fresh.
    """
    text = _normalize_time_text(time_posted)
    if text:
        return text
    print(
        f"[sniper_fallback] listing_id={listing_id} feed_rank={feed_rank} "
        f"empty time_posted -> assuming fresh (0 min, label={SNIPER_FALLBACK_TIME_LABEL!r})",
        flush=True,
    )
    return SNIPER_FALLBACK_TIME_LABEL


_SNIPER_INSTANT_RE = re.compile(
    r"^(?:"
    r"just\s+listed|just\s+now|newly\s+listed|"
    r"(?:à|a)\s*l\s*['']?\s*instant|"
    r"maintenant|"
    r"الآن|الان"
    r")$",
    re.IGNORECASE,
)


def is_sniper_eligible(
    time_posted: str,
    max_minutes: int = DEFAULT_MAX_ALERT_AGE_MINUTES,
    *,
    assume_fresh_if_empty: bool = False,
    sniper_mode: bool = False,
) -> bool:
    """
    Sniper feed mode: accept 'Just listed', 'À l'instant', or numeric age <= max_minutes.

    When assume_fresh_if_empty is True (newest-first feed), empty time is treated as <= max_minutes.
    """
    raw_time = (time_posted or "").strip()
    if sniper_mode and not raw_time:
        print(
            "[time_parse] raw='' allowed=True reason=sniper_mode_empty_bypass",
            flush=True,
        )
        return True
    if assume_fresh_if_empty and not raw_time:
        print(
            "[time_parse] raw='' allowed=True reason=sniper_feed_order_fallback",
            flush=True,
        )
        return True

    cleaned = clean_time_posted_text(time_posted) or _normalize_time_text(time_posted)
    reason = "ok"

    if not cleaned:
        if assume_fresh_if_empty:
            reason = "sniper_feed_order_fallback"
            allowed = True
            parsed_display = 0
        else:
            reason = "empty"
            allowed = False
            parsed_display = "?"
    elif _SNIPER_INSTANT_RE.match(cleaned) or is_just_listed(cleaned):
        reason = "sniper_instant"
        allowed = True
        parsed_display = 0
    elif contains_hour_age_indicator(cleaned):
        reason = "hours_detected"
        age = parse_listing_age_minutes(cleaned)
        parsed_display = "?" if age is None else round(age, 2)
        allowed = False
    else:
        age = parse_listing_age_minutes(cleaned)
        if age is None:
            reason = "unparseable"
            allowed = False
        elif age > max_minutes:
            reason = "over_max_age"
            allowed = False
        else:
            allowed = True
        parsed_display = "?" if age is None else round(age, 2)

    print(
        f"[time_parse] raw={cleaned!r} parsed_minutes={parsed_display} "
        f"allowed={allowed} reason={reason}",
        flush=True,
    )
    return allowed
