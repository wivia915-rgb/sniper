"""
Entry point: poll Facebook Marketplace and send Telegram alerts for new listings.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from dataclasses import replace
from typing import Optional

from config import Config
from database import SeenItemsDatabase
from notifier import TelegramNotifier
from listing_time import sniper_apply_empty_time_fallback
from scraper import MarketplaceScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("marketplace_monitor")


class MonitorApp:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.db = SeenItemsDatabase(config.database_path)
        self.notifier = TelegramNotifier(
            config.telegram_bot_token,
            config.telegram_chat_id,
        )
        self.scraper = MarketplaceScraper(
            config.search_url,
            headless=config.headless,
            user_data_dir=config.browser_user_data_dir,
            page_load_timeout_ms=config.page_load_timeout_ms,
            scroll_passes=config.scroll_passes,
            top_listings_limit=config.top_listings_limit,
            sniper_mode=config.sniper_mode,
            use_graphql=config.use_graphql,
        )
        self._stop = asyncio.Event()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except NotImplementedError:
                # Windows may not support all signals in all contexts.
                pass

        await self.scraper.start()
        mode = (
            f"SNIPER top-{self.config.top_listings_limit} every "
            f"{self.config.check_interval_seconds}s (≤{self.config.max_alert_age_minutes} min)"
            if self.config.sniper_mode
            else "standard poll"
        )
        filter_note = (
            f"time filter ON (≤{self.config.max_alert_age_minutes} min)"
            if self.config.time_filter_enabled
            else "time filter OFF (test mode — all new listings alert)"
        )
        logger.info("%s | %s", mode, filter_note)
        self.notifier.send_status(
            f"Marketplace sniper started. {mode}. {filter_note}"
        )

        try:
            while not self._stop.is_set():
                await self._poll_once()
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self.config.check_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            try:
                await self.scraper.stop()
            except Exception as exc:
                logger.warning("Shutdown browser cleanup error (ignored): %s", exc)
            logger.info("Monitor stopped.")

    async def _ensure_scraper_running(self) -> bool:
        if self.scraper.is_running:
            return True
        try:
            await self.scraper.start()
            return True
        except Exception as exc:
            logger.exception("Failed to start browser: %s", exc)
            return False

    async def _recover_browser(self, exc: Exception) -> None:
        if not MarketplaceScraper.is_browser_disconnect_error(exc):
            return
        logger.warning("Attempting browser recovery after disconnect...")
        try:
            await self.scraper.restart()
            logger.info("Browser session recovered.")
        except Exception as restart_exc:
            logger.exception("Browser recovery failed: %s", restart_exc)
            self.notifier.send_status(f"Browser recovery failed: {restart_exc}")

    async def _poll_once(self) -> None:
        if not await self._ensure_scraper_running():
            self.notifier.send_status("Browser unavailable — will retry next cycle.")
            return

        try:
            listings = await self.scraper.fetch_listings()
        except Exception as exc:
            logger.exception("Scrape failed: %s", exc)
            await self._recover_browser(exc)
            self.notifier.send_status(f"Scrape error: {exc}")
            return

        new_count = 0
        max_age = self.config.max_alert_age_minutes
        skipped_seen = 0
        skipped_time = 0
        failed_telegram = 0

        for rank, item in enumerate(listings, start=1):
            if self.db.has_seen(item.listing_id):
                skipped_seen += 1
                print(
                    f"[alert_skip] listing_id={item.listing_id} reason=already_seen",
                    flush=True,
                )
                continue

            raw_time_for_filter = item.time_posted

            if not self.config.sniper_mode:
                search_hint = item.time_posted
                resolved_time = await self.scraper.resolve_listing_time_posted(
                    item.url,
                    search_hint,
                )
                item = replace(item, time_posted=resolved_time)
                raw_time_for_filter = item.time_posted
                print(
                    f"[time_resolve] listing_id={item.listing_id} "
                    f"search_card_hint={search_hint!r} resolved={resolved_time!r} "
                    f"using={item.time_posted!r}",
                    flush=True,
                )
            else:
                print(
                    f"[sniper] listing_id={item.listing_id} feed_rank={rank} "
                    f"feed_time={item.time_posted!r}",
                    flush=True,
                )

            time_ok = self.scraper.is_listing_within_alert_window(
                raw_time_for_filter,
                sniper_mode=self.config.sniper_mode,
                time_filter_enabled=self.config.time_filter_enabled,
                max_alert_age_minutes=max_age,
                creation_time_unix=item.creation_time_unix,
            )

            if self.config.sniper_mode:
                item = replace(
                    item,
                    time_posted=sniper_apply_empty_time_fallback(
                        item.time_posted,
                        listing_id=item.listing_id,
                        feed_rank=rank,
                    ),
                )

            if self.config.time_filter_enabled and not time_ok:
                skipped_time += 1
                print(
                    f"[alert_skip] listing_id={item.listing_id} "
                    f"reason=time_filter_failed time_posted={item.time_posted!r}",
                    flush=True,
                )
                continue
            elif not self.config.sniper_mode and not item.time_posted:
                logger.info(
                    "Listing %s has no time_posted text (filter disabled — alerting anyway)",
                    item.listing_id,
                )

            print(
                f"[alert_send] listing_id={item.listing_id} time_posted={item.time_posted!r}",
                flush=True,
            )
            if self.notifier.send_telegram_alert(item):
                self.db.mark_seen(
                    item.listing_id,
                    item.url,
                    item.title,
                    item.price,
                    item.location,
                )
                new_count += 1
                logger.info("Alert sent: %s (%s)", item.title, item.listing_id)
            else:
                failed_telegram += 1
                print(
                    f"[alert_skip] listing_id={item.listing_id} reason=telegram_failed",
                    flush=True,
                )
                logger.error("Failed to notify for listing %s", item.listing_id)

        print(
            f"[poll_summary] scraped={len(listings)} sent={new_count} "
            f"already_seen={skipped_seen} time_filter_failed={skipped_time} "
            f"telegram_failed={failed_telegram}",
            flush=True,
        )
        if new_count:
            logger.info("Sent %d new alert(s)", new_count)
        else:
            logger.debug("No new listings this cycle.")


def main() -> int:
    try:
        config = Config.from_env()
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    app = MonitorApp(config)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
