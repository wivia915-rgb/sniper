"""
Facebook Marketplace search scraper using Playwright (async).

Note: Facebook often requires a logged-in session for Marketplace. Use a persistent
browser profile (BROWSER_USER_DATA_DIR) and log in once manually if needed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from graphql_search import (
    CapturedGraphQLRequest,
    capture_from_playwright_request,
    is_marketplace_search_request,
    is_marketplace_search_response,
    parse_marketplace_listings_from_response,
)
from listing_time import (
    clean_time_posted_text,
    extract_time_phrase,
    is_promotional_unverified_label,
    sniper_apply_empty_time_fallback,
)
from marketplace_url import ensure_newest_first_sort
from models import MarketplaceItem

logger = logging.getLogger(__name__)

LISTING_ID_RE = re.compile(r"/marketplace/item/(\d+)")
FB_BASE = "https://www.facebook.com"

# Realistic desktop Chrome fingerprint (rotate periodically in production).
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_LOCALE = "en-US"
DEFAULT_TIMEZONE = "America/New_York"

# When True, only listings within MAX_ALERT_AGE_MINUTES trigger Telegram alerts.
ENABLE_TIME_FILTER = True
MAX_ALERT_AGE_MINUTES = 10
DEFAULT_CHECK_INTERVAL = 15
DEFAULT_TOP_LISTINGS = 5
DEFAULT_SCROLL_PASSES = 0

def extract_listing_id(url: str) -> Optional[str]:
    match = LISTING_ID_RE.search(url)
    return match.group(1) if match else None


def normalize_listing_url(href: str) -> str:
    if href.startswith("/"):
        return urljoin(FB_BASE, href.split("?")[0])
    parsed = urlparse(href)
    if "facebook.com" in parsed.netloc:
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    return href.split("?")[0]


# Fallback when a parent node still merges card fields (e.g. "Just listedMAD14,000...").
_TIME_BLOB_PREFIX_RE = re.compile(
    r"^(?P<t>"
    r"just\s+listed|just\s+now|newly\s+listed|"
    r"a\s+few\s+seconds\s+ago|"
    r"\d+\s*mins?\s+ago|\d+\s*minutes?\s+ago|"
    r"\d+\s*m\b|"
    r"(?:à|a)\s*l\s*['']?\s*instant|"
    r"il\s+y\s+a\s+\d+\s*min(?:ute)?s?|"
    r"il\s+y\s+a\s+quelques\s+secondes|"
    r"maintenant|"
    r"الآن|الان|منذ\s+\d+\s*د"
    r")",
    re.IGNORECASE,
)


def salvage_time_posted(raw: str) -> str:
    """Extract a relative-time phrase from an over-captured card string."""
    cleaned = clean_time_posted_text(raw)
    if cleaned:
        return cleaned
    if not raw:
        return ""
    text = raw.strip()
    match = _TIME_BLOB_PREFIX_RE.match(text)
    if match:
        return match.group("t").strip()
    return ""


# JavaScript: extract relative listing time from a product detail page.
_DETAIL_PAGE_TIME_SCRIPT = """
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

    // 1) Leaf text nodes (original approach)
    document.querySelectorAll('span[dir="auto"], div[dir="auto"], span, div').forEach((el) => {
        if (el.children.length > 0) return;
        pushCandidate(el.textContent || '');
    });

    // 2) Elements whose visible text looks like listing metadata (may have nested spans)
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

    // 3) Full page text fallback (handles split/hidden DOM)
    const body = (document.body?.innerText || '').slice(0, 25000);
    for (const re of EXTRACT) {
        const m = body.match(re);
        if (m) return m[1].trim();
    }
    const loose = body.match(/(listed\\s+(?:about\\s+)?(?:an\\s+hour|\\d+\\s*\\w+?)\\s+ago)/i);
    if (loose) return loose[1].trim();

    return '';
}
"""


def _evaluate_listing_alert_window(
    raw_time: str,
    *,
    sniper_mode: bool,
    time_filter_enabled: bool,
    max_alert_age_minutes: int,
    creation_time_unix: int | None = None,
) -> bool:
    """
    Central time gate for the poll loop.

    SNIPER_MODE: empty/missing card time is allowed immediately (newest-first feed + DB dedup).
    """
    import time as time_mod

    from listing_time import is_sniper_eligible, is_within_time_limit

    if not time_filter_enabled:
        return True

    raw = (raw_time or "").strip()
    if sniper_mode and not raw:
        print(
            "[time_parse] raw='' allowed=True reason=sniper_mode_empty_bypass",
            flush=True,
        )
        return True

    if creation_time_unix is not None:
        age_min = (time_mod.time() - creation_time_unix) / 60.0
        allowed = age_min <= max_alert_age_minutes
        print(
            f"[graphql_time] creation_time={creation_time_unix} age_min={age_min:.2f} "
            f"allowed={allowed}",
            flush=True,
        )
        return allowed

    if sniper_mode:
        return is_sniper_eligible(
            raw_time,
            max_alert_age_minutes,
            assume_fresh_if_empty=True,
            sniper_mode=True,
        )
    return is_within_time_limit(raw_time, max_alert_age_minutes)


def normalize_verified_time_posted(raw: str, *, allow_promotional: bool = False) -> str:
    """
    Keep only verifiable relative times; drop promotional search-card labels.
    """
    cleaned = clean_time_posted_text((raw or "").strip())
    if not cleaned:
        phrase = extract_time_phrase((raw or "").strip())
        cleaned = phrase or ""
    if not cleaned:
        return ""
    if not allow_promotional and is_promotional_unverified_label(cleaned):
        print(
            f"[time_posted] ignored promotional_unverified label={cleaned!r}",
            flush=True,
        )
        return ""
    return cleaned


class MarketplaceScraper:
    """Async Playwright scraper with basic anti-detection settings."""

    def __init__(
        self,
        search_url: str,
        *,
        headless: bool = True,
        user_data_dir: str = "",
        page_load_timeout_ms: int = 60_000,
        scroll_passes: int = DEFAULT_SCROLL_PASSES,
        top_listings_limit: int = DEFAULT_TOP_LISTINGS,
        sniper_mode: bool = True,
        use_graphql: bool = True,
    ) -> None:
        self.search_url = ensure_newest_first_sort(search_url)
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.page_load_timeout_ms = page_load_timeout_ms
        self.scroll_passes = scroll_passes
        self.top_listings_limit = top_listings_limit
        self.sniper_mode = sniper_mode
        self.use_graphql = use_graphql
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._feed_page: Optional[Page] = None
        self._feed_ready = False
        self._graphql_template: Optional[CapturedGraphQLRequest] = None
        self._graphql_listener_attached = False
        self._last_graphql_body = ""

    @staticmethod
    def is_browser_disconnect_error(exc: BaseException) -> bool:
        """True when Playwright/Chromium closed or the driver connection dropped."""
        message = str(exc).lower()
        markers = (
            "connection closed",
            "target closed",
            "browser has been closed",
            "browser closed",
            "disconnected",
            "pipe closed",
            "execution context was destroyed",
        )
        return any(marker in message for marker in markers)

    async def _safe_close_page(self, page: Optional[Page]) -> None:
        if page is None:
            return
        try:
            if not page.is_closed():
                await page.close()
        except Exception as exc:
            logger.warning("Error closing page (ignored): %s", exc)
            if self.is_browser_disconnect_error(exc):
                self._invalidate_browser()

    def _invalidate_browser(self) -> None:
        """Clear stale handles after a crash/disconnect without calling close()."""
        self._context = None
        self._browser = None
        self._graphql_template = None
        self._graphql_listener_attached = False

    def _attach_graphql_listener(self, page: Page) -> None:
        if self._graphql_listener_attached:
            return

        def _schedule(response) -> None:
            asyncio.create_task(self._handle_graphql_response(response))

        page.on("response", _schedule)
        self._graphql_listener_attached = True
        logger.debug("GraphQL network listener attached")

    async def _handle_graphql_response(self, response) -> None:
        try:
            request = response.request
            if request.method != "POST":
                return
            if "graphql" not in response.url.lower():
                return
            headers = dict(request.headers)
            post_data = request.post_data or ""
            if not is_marketplace_search_request(post_data, headers):
                return
            body = await response.text()
            if not is_marketplace_search_response(body):
                return
            self._graphql_template = capture_from_playwright_request(request)
            self._last_graphql_body = body
            logger.info(
                "Captured Marketplace GraphQL template (%s)",
                self._graphql_template.friendly_name or "unknown",
            )
        except Exception as exc:
            logger.debug("GraphQL response handler skipped: %s", exc)

    async def _ensure_graphql_template(self, timeout_seconds: float = 20.0) -> bool:
        if self._graphql_template:
            return True
        if not self._feed_page or self._feed_page.is_closed():
            await self._open_warm_feed_page(initial=True)
        assert self._feed_page is not None
        self._attach_graphql_listener(self._feed_page)
        await self._feed_page.reload(wait_until="domcontentloaded")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            if self._graphql_template:
                return True
            await asyncio.sleep(0.25)
        logger.warning("Timed out waiting for Marketplace GraphQL capture")
        return False

    async def _fetch_listings_graphql(self) -> List[MarketplaceItem]:
        if not self._context or not self._graphql_template:
            return []
        template = self._graphql_template
        try:
            response = await self._context.request.post(
                template.url,
                headers=template.headers,
                data=template.post_data,
            )
            if not response.ok:
                logger.warning(
                    "GraphQL replay HTTP %s: %s",
                    response.status,
                    (await response.text())[:200],
                )
                return []
            raw = await response.text()
            items = parse_marketplace_listings_from_response(
                raw,
                limit=self.top_listings_limit,
            )
            print(
                f"[graphql] replay ok listings={len(items)} "
                f"template={template.friendly_name or 'unknown'}",
                flush=True,
            )
            for rank, item in enumerate(items, start=1):
                if self.sniper_mode and not (item.time_posted or "").strip():
                    updated = sniper_apply_empty_time_fallback(
                        item.time_posted,
                        listing_id=item.listing_id,
                        feed_rank=rank,
                    )
                    item = MarketplaceItem(
                        listing_id=item.listing_id,
                        title=item.title,
                        price=item.price,
                        location=item.location,
                        url=item.url,
                        image_url=item.image_url,
                        time_posted=updated,
                        creation_time_unix=item.creation_time_unix,
                    )
                    items[rank - 1] = item
                print(
                    f"[graphql] id={item.listing_id} creation_time={item.creation_time_unix} "
                    f"time_posted={item.time_posted!r} title={item.title!r} price={item.price!r}",
                    flush=True,
                )
            return items
        except Exception as exc:
            logger.warning("GraphQL replay failed: %s", exc)
            if self.is_browser_disconnect_error(exc):
                self._invalidate_browser()
            return []

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]

        if self.user_data_dir:
            Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=self.headless,
                args=launch_args,
                user_agent=DEFAULT_USER_AGENT,
                viewport={"width": 1366, "height": 768},
                locale=DEFAULT_LOCALE,
                timezone_id=DEFAULT_TIMEZONE,
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Upgrade-Insecure-Requests": "1",
                },
            )
        else:
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=launch_args,
            )
            self._context = await self._browser.new_context(
                user_agent=DEFAULT_USER_AGENT,
                viewport={"width": 1366, "height": 768},
                locale=DEFAULT_LOCALE,
                timezone_id=DEFAULT_TIMEZONE,
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )

        await self._context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """
        )
        logger.info("Browser context started (headless=%s)", self.headless)
        await self._open_warm_feed_page(initial=True)
        if self.use_graphql:
            await self._ensure_graphql_template(timeout_seconds=20.0)

    async def _open_warm_feed_page(self, *, initial: bool = False) -> None:
        """Keep one search tab open for fast reload-based sniper polls."""
        if not self._context:
            return
        if self._feed_page and not self._feed_page.is_closed():
            if not initial:
                return
        else:
            self._feed_page = await self._context.new_page()
            self._feed_page.set_default_timeout(self.page_load_timeout_ms)
            self._graphql_listener_attached = False

        assert self._feed_page is not None
        if self.use_graphql:
            self._attach_graphql_listener(self._feed_page)
        await self._feed_page.goto(self.search_url, wait_until="domcontentloaded")
        await self._dismiss_cookie_banner(self._feed_page)
        self._feed_ready = True
        logger.info(
            "Warm feed page ready (sniper=%s, top=%d, graphql=%s)",
            self.sniper_mode,
            self.top_listings_limit,
            self.use_graphql,
        )

    async def stop(self) -> None:
        await self._safe_close_page(self._feed_page)
        self._feed_page = None
        self._feed_ready = False

        context = self._context
        browser = self._browser
        playwright = self._playwright
        self._context = None
        self._browser = None
        self._playwright = None

        if context is not None:
            try:
                await context.close()
            except Exception as exc:
                logger.warning("Error closing browser context (ignored): %s", exc)

        if browser is not None:
            try:
                await browser.close()
            except Exception as exc:
                logger.warning("Error closing browser (ignored): %s", exc)

        if playwright is not None:
            try:
                await playwright.stop()
            except Exception as exc:
                logger.warning("Error stopping Playwright (ignored): %s", exc)

    async def restart(self, *, backoff_seconds: float = 3.0) -> None:
        """Stop cleanly (best effort) and launch a fresh browser session."""
        logger.info("Restarting browser session...")
        await self.stop()
        if backoff_seconds > 0:
            await asyncio.sleep(backoff_seconds)
        await self.start()

    @property
    def is_running(self) -> bool:
        return self._context is not None

    def is_listing_within_alert_window(
        self,
        raw_time: str,
        *,
        sniper_mode: bool,
        time_filter_enabled: bool,
        max_alert_age_minutes: int,
        creation_time_unix: int | None = None,
    ) -> bool:
        """Validate listing age for alerts (delegates to shared time-gate logic)."""
        return _evaluate_listing_alert_window(
            raw_time,
            sniper_mode=sniper_mode,
            time_filter_enabled=time_filter_enabled,
            max_alert_age_minutes=max_alert_age_minutes,
            creation_time_unix=creation_time_unix,
        )

    async def fetch_listings(self) -> List[MarketplaceItem]:
        """Poll top-N listings via GraphQL replay (fast) with DOM fallback."""
        if not self._context:
            raise RuntimeError("Scraper not started — call start() first")

        try:
            if self.use_graphql:
                if not self._graphql_template:
                    await self._ensure_graphql_template()
                items = await self._fetch_listings_graphql()
                if items:
                    logger.info(
                        "GraphQL sniper returned %d listing(s) (limit=%d)",
                        len(items),
                        self.top_listings_limit,
                    )
                    return items
                logger.warning("GraphQL sniper empty — falling back to DOM parse")

            await self._open_warm_feed_page()
            assert self._feed_page is not None
            page = self._feed_page

            if self._feed_ready:
                logger.debug("Reloading warm feed %s", self.search_url)
                await page.reload(wait_until="domcontentloaded")
            else:
                await page.goto(self.search_url, wait_until="domcontentloaded")
                await self._dismiss_cookie_banner(page)

            if self.scroll_passes > 0:
                await self._scroll_results(page)

            items = await self._parse_listings(page)
            logger.info(
                "DOM sniper parsed %d top listing(s) (limit=%d)",
                len(items),
                self.top_listings_limit,
            )
            return items
        except Exception as exc:
            if self.is_browser_disconnect_error(exc):
                logger.warning("Browser disconnected during scrape: %s", exc)
                self._invalidate_browser()
                self._feed_page = None
                self._feed_ready = False
            raise

    async def fetch_accurate_time_posted(self, listing_url: str) -> str:
        """
        Open the listing detail page and read the true relative time
        (e.g. 'Listed 56 minutes ago' vs search card 'Just listed').
        """
        if not self._context:
            raise RuntimeError("Scraper not started — call start() first")

        page: Optional[Page] = None
        try:
            page = await self._context.new_page()
            page.set_default_timeout(self.page_load_timeout_ms)
            await page.goto(listing_url, wait_until="domcontentloaded")
            await self._dismiss_cookie_banner(page)
            try:
                await page.wait_for_function(
                    """
                    () => {
                        const t = document.body?.innerText || '';
                        return /listed|ago|il y a|min|heure|دقيقة|منذ/i.test(t);
                    }
                    """,
                    timeout=15_000,
                )
            except Exception:
                logger.debug("Detail page time hint not found before timeout")
            await asyncio.sleep(1.0)
            raw = await page.evaluate(_DETAIL_PAGE_TIME_SCRIPT)
            cleaned = normalize_verified_time_posted((raw or "").strip())
            print(
                f"[time_posted detail] url={listing_url} dom_raw={raw!r} parsed={cleaned!r}",
                flush=True,
            )
            return cleaned
        except Exception as exc:
            logger.warning(
                "Detail page time fetch failed for %s: %s", listing_url, exc
            )
            if self.is_browser_disconnect_error(exc):
                self._invalidate_browser()
            return ""
        finally:
            await self._safe_close_page(page)

    async def resolve_listing_time_posted(
        self,
        listing_url: str,
        search_card_hint: str = "",
    ) -> str:
        """
        Prefer detail-page time; fall back to search-card hint when detail is empty.
        """
        detail = await self.fetch_accurate_time_posted(listing_url)
        if detail:
            return detail

        fallback = normalize_verified_time_posted(search_card_hint)
        if fallback:
            print(
                f"[time_posted] detail empty — using search_card fallback={fallback!r}",
                flush=True,
            )
            return fallback

        salvage = extract_time_phrase(search_card_hint)
        if salvage:
            print(
                f"[time_posted] detail empty — using search phrase fallback={salvage!r}",
                flush=True,
            )
            return salvage

        print(
            f"[time_posted] detail and search_card both empty url={listing_url}",
            flush=True,
        )
        return ""

    async def _dismiss_cookie_banner(self, page: Page) -> None:
        for selector in (
            'button[data-cookiebanner="accept_button"]',
            'button:has-text("Allow all cookies")',
            'button:has-text("Accept All")',
            'button:has-text("Allow essential and optional cookies")',
        ):
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                continue

    async def _scroll_results(self, page: Page) -> None:
        """Scroll to load lazy-loaded listing cards."""
        for i in range(self.scroll_passes):
            await page.mouse.wheel(0, 1200)
            await asyncio.sleep(1.2)
            logger.debug("Scroll pass %d/%d", i + 1, self.scroll_passes)

    async def _parse_listings(self, page: Page) -> List[MarketplaceItem]:
        """
        Extract listings from anchor tags pointing to /marketplace/item/{id}.
        DOM structure varies; parsing uses link hrefs plus nearby text/image.
        """
        raw: List[dict] = await page.evaluate(
            """
            ({ limit, sniperMode }) => {
                const MAX_TIME_LEN = 45;

                const TIME_EXACT = [
                    /^just listed$/i,
                    /^just now$/i,
                    /^newly listed$/i,
                    /^a few seconds ago$/i,
                    /^\\d+\\s*s(ec|ecs|econd|econds)?\\s*(ago)?$/i,
                    /^listed\\s+\\d+\\s*minutes?\\s+ago$/i,
                    /^listed\\s+\\d+\\s*mins?\\s+ago$/i,
                    /^\\d+\\s*mins?\\s*ago$/i,
                    /^\\d+\\s*minutes?\\s*ago$/i,
                    /^\\d+\\s*m(in|ins|inute|inutes)?\\s*(ago)?$/i,
                    /^\\d+\\s*h(r|rs|our|ours)?\\s*(ago)?$/i,
                    /^\\d+\\s*heures?\\s*(ago)?$/i,
                    /^(?:à|a)\\s*l\\s*['']?\\s*instant$/i,
                    /^maintenant$/i,
                    /^il y a \\d+\\s*min(?:ute)?s?$/i,
                    /^il y a \\d+\\s*heures?$/i,
                    /^il y a quelques secondes$/i,
                    /^الآن$/i,
                    /^الان$/i,
                    /^منذ\\s+\\d+\\s*د(?:قيقة|قائق)?$/i,
                    /^منذ\\s+\\d+\\s*س(?:اعة|اعات)?$/i,
                ];

                const hasListingNoise = (t) =>
                    /MAD|DH|USD|EUR|\\$|€|£|\\d{1,3},\\d{3}|\\n|Morocco|Maroc|Casablanca|hidden information/i.test(t);

                const isTimeOnlyText = (t) => {
                    t = (t || '').trim();
                    if (!t || t.length > MAX_TIME_LEN) return false;
                    if (hasListingNoise(t)) return false;
                    return TIME_EXACT.some((re) => re.test(t));
                };

                const collectLeafTexts = (root) => {
                    const out = [];
                    if (!root) return out;
                    root.querySelectorAll('span[dir="auto"], span, div[dir="auto"]').forEach((el) => {
                        if (el.children.length > 0) return;
                        const t = (el.textContent || '').trim();
                        if (t) out.push(t);
                    });
                    return out;
                };

                const isPromotional = (t) => /^just\\s+listed$|^newly\\s+listed$/i.test(t);

                const findTimePosted = (roots) => {
                    for (const root of roots) {
                        const leaves = collectLeafTexts(root);
                        for (const t of leaves) {
                            if (!sniperMode && isPromotional(t)) continue;
                            if (isTimeOnlyText(t) || (sniperMode && isPromotional(t))) return t;
                        }
                    }
                    return '';
                };

                const isPriceText = (t) =>
                    /^(\\$|€|£|FREE|MAD\\s*\\d|MAD\\d|\\d{1,3}(,\\d{3})+|Free)$/i.test(t.trim());

                const isLocationText = (t) =>
                    t.length < 100 && /,/.test(t) && !isPriceText(t) && !isTimeOnlyText(t);

                const pickTitle = (leaves, price, location, timePosted) =>
                    leaves
                        .filter(
                            (t) =>
                                t !== price &&
                                t !== location &&
                                t !== timePosted &&
                                !isPriceText(t) &&
                                !isTimeOnlyText(t) &&
                                !isLocationText(t) &&
                                t.length > 2
                        )
                        .sort((a, b) => b.length - a.length)[0] || '';

                const anchors = Array.from(
                    document.querySelectorAll('a[href*="/marketplace/item/"]')
                );
                const seen = new Set();
                const results = [];

                for (const a of anchors) {
                    const href = a.getAttribute('href') || '';
                    const idMatch = href.match(/\\/marketplace\\/item\\/(\\d+)/);
                    if (!idMatch) continue;
                    const id = idMatch[1];
                    if (seen.has(id)) continue;
                    seen.add(id);

                    let card = a;
                    for (let i = 0; i < 6 && card.parentElement; i++) {
                        card = card.parentElement;
                    }

                    const img = a.querySelector('img') || card.querySelector('img');
                    const imageUrl = img ? (img.src || img.getAttribute('src') || '') : '';

                    const searchRoots = [a, card];
                    const leaves = [];
                    for (const root of searchRoots) {
                        for (const t of collectLeafTexts(root)) {
                            if (!leaves.includes(t)) leaves.push(t);
                        }
                    }

                    const timePosted = findTimePosted(searchRoots);

                    let price = leaves.find((t) => isPriceText(t)) || '';
                    let location = leaves.find((t) => isLocationText(t)) || '';
                    let title = pickTitle(leaves, price, location, timePosted);

                    if (!title) {
                        title = (a.getAttribute('aria-label') || '').trim();
                    }

                    results.push({
                        listing_id: id,
                        href,
                        title,
                        price,
                        location,
                        image_url: imageUrl,
                        time_posted: timePosted,
                    });
                    if (results.length >= limit) break;
                }
                return results.slice(0, limit);
            }
            """,
            {
                "limit": self.top_listings_limit,
                "sniperMode": self.sniper_mode,
            },
        )

        items: List[MarketplaceItem] = []
        for row in raw:
            listing_id = row.get("listing_id") or ""
            href = row.get("href") or ""
            if not listing_id:
                continue
            url = normalize_listing_url(href)
            title = (row.get("title") or "Untitled listing").strip()
            search_hint = salvage_time_posted((row.get("time_posted") or "").strip())
            if self.sniper_mode:
                time_posted = search_hint or normalize_verified_time_posted(
                    search_hint, allow_promotional=True
                )
                if not time_posted:
                    time_posted = sniper_apply_empty_time_fallback(
                        "",
                        listing_id=listing_id,
                        feed_rank=len(items) + 1,
                    )
            else:
                time_posted = normalize_verified_time_posted(search_hint)
            print(
                f"[time_posted extract] listing_id={listing_id} "
                f"search_hint={search_hint!r} verified={time_posted!r}",
                flush=True,
            )
            items.append(
                MarketplaceItem(
                    listing_id=listing_id,
                    title=title,
                    price=(row.get("price") or "Price not listed").strip(),
                    location=(row.get("location") or "Unknown").strip(),
                    url=url,
                    image_url=(row.get("image_url") or "").strip(),
                    time_posted=time_posted,
                )
            )
        return items
