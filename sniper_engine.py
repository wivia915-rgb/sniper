"""Background Playwright sniper loop (sync API, runs in a worker thread)."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Deque, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from marketplace_url import ensure_newest_first_sort
from notifier import TelegramNotifier
from seen_listings import load_seen, mark_seen
from sniper_config import SniperSettings, marketplace_search_urls, marketplace_vehicles_url
from marketplace_detail_time import (
    fetch_detail_time_posted,
    listing_passes_time_gate,
    should_mark_seen_after_time_reject,
)
from scan_tuning import ScanTuning, scan_tuning_from_settings
from vehicle_dom import (
    _HOUR_AGE_RE,
    _OLD_AGE_RE,
    card_time_needs_detail_verification,
    is_motorcycle_listing,
    is_vehicle_listing_fresh,
    listing_time_sort_rank,
    parse_listings_from_page,
)

logger = logging.getLogger(__name__)

PROFILE_DIR = Path(__file__).resolve().parent / "data" / "streamlit_browser_profile"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_NETWORK_ERR_MARKERS = (
    "ERR_NAME_NOT_RESOLVED",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_NETWORK_CHANGED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_REFUSED",
    "ERR_TIMED_OUT",
    "NS_ERROR_UNKNOWN_HOST",
)
_NETWORK_COOLDOWN_SEC = 45
_playwright_install_lock = threading.Lock()
_playwright_chromium_ready = False


def _ensure_playwright_chromium_installed() -> None:
    """Install Chromium browser bundle (Streamlit Cloud / fresh deploys)."""
    global _playwright_chromium_ready
    with _playwright_install_lock:
        if _playwright_chromium_ready:
            return
        try:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True,
                env=os.environ.copy(),
            )
            _playwright_chromium_ready = True
        except Exception as e:
            print(f"Error installing playwright browsers: {e}", flush=True)
            logger.warning("Playwright chromium install failed: %s", e)


def _install_fast_network_routes(context) -> None:
    if getattr(context, "_sniper_fast_routes", False):
        return

    def _route_handler(route) -> None:
        if route.request.resource_type in ("image", "media", "font"):
            route.abort()
        else:
            route.continue_()

    context.route("**/*", _route_handler)
    context._sniper_fast_routes = True  # type: ignore[attr-defined]


def _feeds_for_scan_cycle(
    feed_urls: list[str], rotation: int, tuning: ScanTuning
) -> tuple[list[str], int]:
    """Always hit priority feeds; rotate through the rest each cycle."""
    per_cycle = tuning.keywords_per_cycle
    n = len(feed_urls)
    if n <= per_cycle:
        return feed_urls, rotation
    prio_n = min(tuning.priority_feeds, n)
    priority = feed_urls[:prio_n]
    rest = feed_urls[prio_n:]
    slots = max(0, per_cycle - len(priority))
    if not rest or slots == 0:
        return priority, rotation
    batch_rest = [rest[(rotation + i) % len(rest)] for i in range(slots)]
    rotation = (rotation + slots) % len(rest)
    return priority + batch_rest, rotation


def _is_network_error(exc: BaseException) -> bool:
    text = str(exc)
    return any(m in text for m in _NETWORK_ERR_MARKERS)


def _running_on_streamlit_cloud() -> bool:
    env = os.environ
    if env.get("STREAMLIT_RUNTIME_ENVIRONMENT", "").lower() == "cloud":
        return True
    if env.get("STREAMLIT_SHARING_MODE"):
        return True
    if env.get("STREAMLIT_SERVER_PORT") and not env.get("DISPLAY"):
        return True
    return False


def _launch_headless_and_args(settings: SniperSettings) -> tuple[bool, list[str]]:
    """Headless + sandbox flags required on Linux / Streamlit Cloud (no display)."""
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ]
    headless = bool(settings.get("headless", True))
    on_cloud = _running_on_streamlit_cloud()
    if on_cloud or sys.platform.startswith("linux"):
        headless = True
        args.extend(["--no-sandbox", "--disable-gpu"])
    if on_cloud:
        args.append("--disable-software-rasterizer")
    return headless, args


class SniperEngine:
    """Thread-safe vehicle sniper with status and log ring buffer."""

    def __init__(
        self,
        *,
        profile_dir: Path | None = None,
        seen_path: Path | None = None,
    ) -> None:
        self._profile_dir = profile_dir or PROFILE_DIR
        self._seen_path = seen_path or (
            Path(__file__).resolve().parent / "seen_listings.json"
        )
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.status: str = "Paused"
        self.last_error: str = ""
        self.last_scan_at: str = ""
        self.last_scan_summary: str = ""
        self.alerts_sent: int = 0
        self.logs: Deque[str] = deque(maxlen=200)
        self._playwright = None
        self._context = None

    def _log(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {msg}"
        with self._lock:
            self.logs.appendleft(line)
        logger.info(msg)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, settings: SniperSettings) -> bool:
        with self._lock:
            if self.is_running():
                self._log("Sniper already running.")
                return False
            self._stop.clear()
            self.status = "Running"
            self.last_error = ""
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(settings,),
                name="MarketplaceSniper",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop.set()
        self.status = "Stopping..."
        self._log("Stop requested — closing browser...")
        self._close_browser()

    def _safe_goto(self, page, url: str, *, retries: int = 3) -> None:
        last: BaseException | None = None
        for attempt in range(1, retries + 1):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                return
            except (PlaywrightError, PlaywrightTimeout) as exc:
                last = exc
                if not _is_network_error(exc) or attempt >= retries:
                    raise
                wait = 2 * attempt
                self._log(
                    f"Network/DNS error (attempt {attempt}/{retries}), retry in {wait}s…"
                )
                time.sleep(wait)
        if last:
            raise last

    def _launch_browser(self, settings: SniperSettings):
        _ensure_playwright_chromium_installed()
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        if not self._playwright:
            self._playwright = sync_playwright().start()
        headless, chromium_args = _launch_headless_and_args(settings)
        if headless and not settings.get("headless"):
            self._log("Linux/Cloud: forcing headless browser (no display on server).")
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._profile_dir),
            headless=headless,
            viewport={"width": 1280, "height": 900},
            user_agent=USER_AGENT,
            locale="fr-FR",
            timezone_id="Africa/Casablanca",
            args=chromium_args,
        )
        tuning = scan_tuning_from_settings(settings)
        if tuning.block_heavy_resources:
            _install_fast_network_routes(self._context)
        page = self._context.pages[0] if self._context.pages else self._context.new_page()
        page.set_default_timeout(45_000)
        return page

    def _restart_browser(self, settings: SniperSettings):
        self._log("Restarting browser after network error…")
        self._close_browser()
        time.sleep(3)
        page = self._launch_browser(settings)
        if self._has_facebook_auth_cookie(page):
            self._log("Browser restarted — session cookie OK.")
            return page
        if not self._ensure_facebook_login(page, settings):
            self._log("Re-login failed after browser restart.")
            if self._has_facebook_auth_cookie(page):
                return page
            return None
        return page

    def _close_browser(self) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception as exc:
            logger.debug("context close: %s", exc)
        finally:
            self._context = None
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception as exc:
            logger.debug("playwright stop: %s", exc)
        finally:
            self._playwright = None

    def _run_loop(self, settings: SniperSettings) -> None:
        seen = load_seen(self._seen_path)
        feed_rotation = 0
        try:
            page = self._launch_browser(settings)

            if not self._ensure_facebook_login(page, settings):
                self.status = "Paused"
                return

            feed_urls = marketplace_search_urls(
                settings["city"], settings.get("search_keywords") or []
            )
            kws = settings.get("search_keywords") or []
            if kws:
                preview = ", ".join(f'"{k}"' for k in kws[:8])
                if len(kws) > 8:
                    preview += "…"
                self._log(f"Target feeds: {len(feed_urls)} searches — {preview}")
            else:
                self._log(f"Target feed: {feed_urls[0]}")
            notifier = TelegramNotifier(
                settings["telegram_bot_token"],
                settings["telegram_chat_id"],
            )
            tuning = scan_tuning_from_settings(settings)
            interval = max(10, settings["check_interval_seconds"])
            if settings.get("turbo_mode"):
                interval = max(10, min(interval, 12))
                self._log("⚡ Turbo ON — fast feeds, blocked images, priority sort")
            limit = settings["top_listings_limit"]
            max_age = settings["max_alert_age_minutes"]
            time_filter = bool(settings.get("time_filter_enabled"))
            if time_filter:
                self._log(f"Time filter ON — max age {max_age} min")
            else:
                self._log("Time filter OFF — instant alerts (dedup only)")
            self._log(
                f"Scan plan: up to {tuning.keywords_per_cycle} feeds/cycle "
                f"({tuning.priority_feeds} priority + rotate), "
                f"top {limit} listings/feed, detail≤{tuning.max_detail_checks}"
            )
            if time_filter and limit < 12:
                self._log(
                    "Tip: Top listings ≥ 12 (Settings) catches more fresh IDs per search."
                )

            while not self._stop.is_set():
                try:
                    cycle_sent = 0
                    cycle_seen = 0
                    cycle_age = 0
                    cycle_car = 0
                    batch, feed_rotation = _feeds_for_scan_cycle(
                        feed_urls, feed_rotation, tuning
                    )
                    for feed_url in batch:
                        if self._stop.is_set():
                            break
                        sent, n_seen, n_age, n_car = self._scan_once(
                            page,
                            feed_url,
                            limit,
                            max_age,
                            seen,
                            notifier,
                            moto_keywords=settings.get("search_keywords") or [],
                            time_filter_enabled=time_filter,
                            tuning=tuning,
                        )
                        cycle_sent += sent
                        cycle_seen += n_seen
                        cycle_age += n_age
                        cycle_car += n_car
                        time.sleep(tuning.between_feeds_sec)
                    summary = (
                        f"{cycle_sent} new → Telegram | "
                        f"{cycle_seen} déjà vus | {cycle_age} âge | "
                        f"{cycle_car} voitures"
                    )
                    with self._lock:
                        self.last_scan_summary = summary
                        self.last_error = ""
                    self.last_scan_at = time.strftime("%Y-%m-%d %H:%M:%S")
                    self._log(f"Scan cycle: {summary}")
                    if cycle_sent == 0 and cycle_seen > 0:
                        self._log(
                            "Normal: même annonces en tête — alerte dès qu’un NOUVEL ID apparaît."
                        )
                except PlaywrightTimeout as exc:
                    self.last_error = str(exc)
                    self._log(f"Timeout: {exc}")
                except PlaywrightError as exc:
                    if _is_network_error(exc):
                        self.last_error = (
                            "Réseau/DNS: facebook.com inaccessible (ERR_NAME_NOT_RESOLVED). "
                            "Vérifiez Internet/VPN, puis Stop → Start."
                        )
                        self._log(self.last_error)
                        page = self._restart_browser(settings)
                        if page is None:
                            self._log(
                                f"Pause {_NETWORK_COOLDOWN_SEC}s (DNS/Internet) puis retry…"
                            )
                            if self._stop.wait(_NETWORK_COOLDOWN_SEC):
                                break
                            continue
                        continue
                    self.last_error = str(exc)
                    self._log(f"Playwright error: {exc}")
                    if "closed" in str(exc).lower():
                        page = self._restart_browser(settings)
                        if page is None and self._stop.wait(_NETWORK_COOLDOWN_SEC):
                            break
                        continue
                except Exception as exc:
                    if _is_network_error(exc):
                        self.last_error = (
                            "Réseau/DNS — pas un ban Facebook. Retry auto…"
                        )
                        page = self._restart_browser(settings)
                        if page is None:
                            if self._stop.wait(_NETWORK_COOLDOWN_SEC):
                                break
                            continue
                        continue
                    self.last_error = str(exc)
                    self._log(f"Scan error: {exc}")

                if self._stop.wait(interval):
                    break

        except Exception as exc:
            self.last_error = str(exc)
            self._log(f"Fatal: {exc}")
        finally:
            self._close_browser()
            with self._lock:
                self.status = "Paused"
                self._thread = None
            self._log("Sniper stopped.")

    def _dismiss_facebook_cookies(self, page) -> None:
        """Accept cookie banner when present (blocks login click otherwise)."""
        selectors = (
            'button[data-cookiebanner="accept_button"]',
            'button:has-text("Allow all cookies")',
            'button:has-text("Accept All")',
            'button:has-text("Autoriser tous les cookies")',
            'button:has-text("Tout accepter")',
        )
        for selector in selectors:
            try:
                loc = page.locator(selector)
                if loc.count() == 0:
                    continue
                btn = loc.first
                if btn.is_visible():
                    btn.click(timeout=3_000)
                    page.wait_for_timeout(500)
                    return
            except PlaywrightError:
                continue

    def _click_facebook_login_submit(self, page) -> None:
        """Click Facebook's login submit control (DOM varies by locale / A-B tests)."""
        self._dismiss_facebook_cookies(page)

        css_candidates = (
            "button#loginbutton",
            'input[name="login"][type="submit"]',
            'button[name="login"]',
            'form[data-testid="royal_login_form"] button[type="submit"]',
            'div[data-testid="royal_login_form"] button[type="submit"]',
            'button[type="submit"]',
        )
        for selector in css_candidates:
            try:
                loc = page.locator(selector)
                if loc.count() == 0:
                    continue
                btn = loc.first
                if btn.is_visible():
                    btn.click(timeout=12_000)
                    return
            except PlaywrightError:
                continue

        text_labels = (
            "Log in",
            "Log In",
            "Login",
            "Se connecter",
            "Connexion",
            "تسجيل الدخول",
        )
        for label in text_labels:
            try:
                btn = page.get_by_role("button", name=label, exact=True)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(timeout=12_000)
                    return
            except PlaywrightError:
                continue

        try:
            btn = page.get_by_role("button", name=re.compile(r"log\s*in|se\s*connecter", re.I))
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=12_000)
                return
        except PlaywrightError:
            pass

        try:
            div_btn = page.locator('div[role="button"]').filter(
                has_text=re.compile(r"^(log\s*in|se\s*connecter)$", re.I)
            )
            if div_btn.count() > 0 and div_btn.first.is_visible():
                div_btn.first.click(timeout=12_000)
                return
        except PlaywrightError:
            pass

        # Many FB login forms submit on Enter in the password field.
        pass_input = page.locator('input[name="pass"], input#pass').first
        pass_input.press("Enter")
        self._log("Login submit: used Enter on password field.")

    def _has_facebook_auth_cookie(self, page) -> bool:
        try:
            cookies = page.context.cookies("https://www.facebook.com")
            return any(
                c.get("name") == "c_user" and str(c.get("value", "")).strip()
                for c in cookies
            )
        except Exception:
            return False

    def _page_login_issue(self, page) -> str | None:
        url = (page.url or "").lower()
        if "checkpoint" in url or "challenge" in url:
            return "checkpoint"
        if any(
            token in url
            for token in ("two_factor", "twofactor", "approvals", "confirmemail")
        ):
            return "two_factor"
        error_patterns = (
            re.compile(r"wrong credentials|incorrect password|invalid username", re.I),
            re.compile(r"mot de passe incorrect|identifiants incorrects", re.I),
            re.compile(r"password you entered is incorrect", re.I),
        )
        try:
            body = page.locator("body").inner_text(timeout=3_000)
        except PlaywrightError:
            body = ""
        for pat in error_patterns:
            if pat.search(body):
                return "bad_credentials"
        return None

    def _wait_for_login_outcome(self, page, *, headless: bool) -> str:
        """
        Poll after submit. Returns: logged_in | checkpoint | two_factor |
        bad_credentials | timeout
        """
        max_wait = 45 if headless else 120
        self._log(
            f"Waiting up to {max_wait}s for Facebook login"
            + (" (complete 2FA in the browser window if prompted)." if not headless else ".")
        )
        deadline = time.time() + max_wait
        while time.time() < deadline and not self._stop.is_set():
            issue = self._page_login_issue(page)
            if issue == "bad_credentials":
                return "bad_credentials"
            if issue == "checkpoint":
                return "checkpoint"
            if issue == "two_factor":
                if not headless:
                    self._log("2FA prompt detected — complete it in the Chromium window.")
                else:
                    return "two_factor"
            if self._looks_logged_in(page):
                return "logged_in"
            page.wait_for_timeout(1_000)
        if self._looks_logged_in(page):
            return "logged_in"
        return "timeout"

    def _verify_marketplace_session(self, page, probe_url: str) -> bool:
        try:
            page.goto(probe_url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2000)
            url = (page.url or "").lower()
            if "facebook.com/login" in url:
                return False
            if self._has_facebook_auth_cookie(page):
                return True
            return "/marketplace" in url
        except PlaywrightError:
            return False

    def _ensure_facebook_login(self, page, settings: SniperSettings) -> bool:
        email = settings["facebook_email"].strip()
        password = settings["facebook_password"]
        if not email or not password:
            self._log("Missing Facebook email or password in settings.")
            return False

        try:
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            if self._looks_logged_in(page):
                self._log("Facebook session active (persistent profile).")
                return True

            self._log("Logging in to Facebook...")
            page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            self._dismiss_facebook_cookies(page)
            page.wait_for_selector(
                'input[name="pass"], input#pass',
                state="visible",
                timeout=20_000,
            )
            page.locator('input[name="email"], input#email').first.fill(
                email, timeout=15_000
            )
            page.locator('input[name="pass"], input#pass').first.fill(
                password, timeout=15_000
            )
            self._click_facebook_login_submit(page)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
            except PlaywrightTimeout:
                pass

            outcome = self._wait_for_login_outcome(
                page, headless=bool(settings["headless"])
            )
            if outcome == "logged_in":
                self._log("Login successful.")
                return True
            if outcome == "bad_credentials":
                self._log("Facebook rejected email/password — update Save Settings.")
                return False
            if outcome == "checkpoint":
                self._log(
                    "Security checkpoint — uncheck Headless, Start again, finish verification "
                    "in the browser, then Save & Start."
                )
                return False
            if outcome == "two_factor":
                self._log(
                    "2FA required — uncheck Headless and complete login in the browser window."
                )
                return False

            probe = marketplace_vehicles_url(settings["city"])
            if self._verify_marketplace_session(page, probe):
                self._log("Session OK (marketplace reachable with saved cookies).")
                return True

            self._log(
                "Login not confirmed — URL="
                f"{page.url[:120]} — try Headless off, log in once manually, then restart."
            )
            return False
        except PlaywrightTimeout as exc:
            self.last_error = str(exc)
            self._log(f"Login timeout: {exc}")
            return False

    def _looks_logged_in(self, page) -> bool:
        if self._has_facebook_auth_cookie(page):
            return True

        url = (page.url or "").lower()
        if "facebook.com/login" in url:
            return False

        selectors = (
            '[aria-label="Your profile"]',
            '[aria-label="Votre profil"]',
            '[aria-label="Ton profil"]',
            '[aria-label="Account"]',
            '[aria-label="Compte"]',
            'a[href*="/me/"]',
            'a[aria-label="Marketplace"]',
        )
        for sel in selectors:
            try:
                if page.locator(sel).count() > 0:
                    return True
            except Exception:
                continue
        return "facebook.com/home" in url or "/marketplace" in url

    def _scan_once(
        self,
        page,
        target_url: str,
        limit: int,
        max_age: int,
        seen: set[str],
        notifier: TelegramNotifier,
        *,
        moto_keywords: list[str] | None = None,
        time_filter_enabled: bool = False,
        tuning: ScanTuning | None = None,
    ) -> tuple[int, int, int, int]:
        """Returns (alerts_sent, skipped_seen, skipped_age, skipped_car)."""
        moto_keywords = moto_keywords or []
        if tuning is None:
            tuning = scan_tuning_from_settings({"turbo_mode": False})
        max_detail = tuning.max_detail_checks
        stale_limit = tuning.stale_card_streak
        url = ensure_newest_first_sort(target_url)
        self._safe_goto(page, url)
        page.wait_for_timeout(tuning.feed_wait_ms)

        listings = parse_listings_from_page(page, limit)
        if time_filter_enabled and listings:
            listings = sorted(
                listings,
                key=lambda it: listing_time_sort_rank(it.time_posted or "", max_age),
            )
        if "query=" in target_url:
            from urllib.parse import unquote_plus

            feed_hint = unquote_plus(
                target_url.split("query=")[-1].split("&")[0]
            )
        else:
            feed_hint = "moto"
        sent = 0
        skipped_seen = 0
        skipped_age = 0
        skipped_car = 0
        stale_card_streak = 0
        detail_checks = 0

        for item in listings:
            if self._stop.is_set():
                break
            if item.listing_id in seen:
                skipped_seen += 1
                continue
            if not is_motorcycle_listing(item, moto_keywords):
                skipped_car += 1
                stale_card_streak = 0
                continue
            if time_filter_enabled:
                card_time = item.time_posted or ""
                if card_time and (
                    _OLD_AGE_RE.search(card_time) or _HOUR_AGE_RE.search(card_time)
                ):
                    mark_seen(item.listing_id, seen, self._seen_path)
                    skipped_age += 1
                    stale_card_streak += 1
                    if stale_card_streak >= stale_limit:
                        self._log(
                            f"[{feed_hint}] feed head stale "
                            f"({stale_card_streak} old cards) — skip rest"
                        )
                        break
                    continue
                stale_card_streak = 0
                if not card_time_needs_detail_verification(card_time, max_age):
                    if not is_vehicle_listing_fresh(card_time, max_age):
                        mark_seen(item.listing_id, seen, self._seen_path)
                        skipped_age += 1
                        continue
                else:
                    if detail_checks >= max_detail:
                        self._log(
                            f"Skip (detail budget): {item.listing_id} "
                            f"({max_detail}/feed — retry next cycle)"
                        )
                        skipped_age += 1
                        continue
                    detail_checks += 1
                    try:
                        detail_time = fetch_detail_time_posted(page, item.url)
                    except Exception as exc:
                        if _is_network_error(exc):
                            raise
                        self._log(f"Detail time failed {item.listing_id}: {exc}")
                        skipped_age += 1
                        continue
                    item.time_posted = detail_time or card_time
                    if not listing_passes_time_gate(
                        detail_time, card_time, max_age
                    ):
                        self._log(
                            f"Skip (detail age): {item.listing_id} "
                            f"detail={detail_time!r} card={card_time!r}"
                        )
                        if should_mark_seen_after_time_reject(
                            detail_time, card_time, max_age
                        ):
                            mark_seen(item.listing_id, seen, self._seen_path)
                        skipped_age += 1
                        stale_card_streak += 1
                        if stale_card_streak >= stale_limit:
                            self._log(
                                f"[{feed_hint}] feed stale after detail checks — skip rest"
                            )
                            break
                        continue
                    stale_card_streak = 0
            if notifier.send_telegram_alert(item):
                mark_seen(item.listing_id, seen, self._seen_path)
                with self._lock:
                    self.alerts_sent += 1
                sent += 1
                self._log(f"[{feed_hint}] Alert sent: {item.title[:50]}")
            else:
                self._log(f"Telegram failed for {item.listing_id}")

        if listings:
            self._log(
                f"[{feed_hint}] {len(listings)} parsed → "
                f"{sent} new, {skipped_seen} seen, {skipped_age} age, "
                f"{skipped_car} voitures"
            )
        return sent, skipped_seen, skipped_age, skipped_car


def get_shared_engine() -> SniperEngine:
    """Module-level singleton for Streamlit reruns."""
    if not hasattr(get_shared_engine, "_instance"):
        get_shared_engine._instance = SniperEngine()  # type: ignore[attr-defined]
    return get_shared_engine._instance  # type: ignore[attr-defined]
