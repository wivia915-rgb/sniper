"""
Telegram Bot API notifications for new marketplace listings.
"""

from __future__ import annotations

import html
import logging
from typing import Optional

import requests

from models import MarketplaceItem

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


class TelegramNotifier:
    """Sends formatted listing alerts via Telegram."""

    def __init__(self, bot_token: str, chat_id: str, timeout: int = 30) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout = timeout
        self._api = TELEGRAM_API_BASE.format(token=bot_token)

    def _escape(self, text: str) -> str:
        return html.escape(text or "—", quote=False)

    def format_message(self, item: MarketplaceItem) -> str:
        """HTML caption (Telegram HTML parse mode)."""
        lines = [
            f"<b>{self._escape(item.title)}</b>",
            f"💰 <b>{self._escape(item.price)}</b>",
            f"📍 {self._escape(item.location)}",
        ]
        if item.time_posted:
            lines.append(f"🕐 {self._escape(item.time_posted)}")
        if item.ai_score is not None:
            lines.append(f"🎯 Snipe {item.ai_score}/10")
        if item.ai_summary:
            lines.append(f"🤖 {self._escape(item.ai_summary)}")
        lines.append(f'\n<a href="{html.escape(item.url, quote=True)}">Open listing</a>')
        return "\n".join(lines)

    def send_listing_alert(self, item: MarketplaceItem) -> bool:
        """
        Send photo + caption when image URL is available; otherwise send text only.
        Returns True on success.
        """
        return self.send_telegram_alert(item)

    def send_telegram_alert(self, item: MarketplaceItem) -> bool:
        """Send a listing alert to Telegram (photo + caption or text)."""
        print(
            f"[telegram] send_telegram_alert listing_id={item.listing_id} "
            f"title={item.title!r}",
            flush=True,
        )
        caption = self.format_message(item)
        ok = False
        if item.image_url:
            ok = self._send_photo(item.image_url, caption)
        else:
            ok = self._send_message(caption)
        if ok:
            print(
                f"[telegram] success listing_id={item.listing_id}",
                flush=True,
            )
        else:
            print(
                f"[telegram] failed listing_id={item.listing_id}",
                flush=True,
            )
        return ok

    def _send_photo(self, photo_url: str, caption: str) -> bool:
        payload = {
            "chat_id": self._chat_id,
            "photo": photo_url,
            "caption": caption[:1024],
            "parse_mode": "HTML",
        }
        try:
            resp = requests.post(
                f"{self._api}/sendPhoto",
                json=payload,
                timeout=self._timeout,
            )
            if resp.ok:
                return True
            # Fallback: some FB CDN URLs block Telegram fetchers.
            logger.warning(
                "sendPhoto failed (%s): %s — falling back to text message",
                resp.status_code,
                resp.text[:200],
            )
        except requests.RequestException as exc:
            logger.warning("sendPhoto request error: %s — falling back to text", exc)
        return self._send_message(caption)

    def _send_message(self, text: str) -> bool:
        payload = {
            "chat_id": self._chat_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        try:
            resp = requests.post(
                f"{self._api}/sendMessage",
                json=payload,
                timeout=self._timeout,
            )
            if not resp.ok:
                logger.error("sendMessage failed (%s): %s", resp.status_code, resp.text)
                return False
            return True
        except requests.RequestException as exc:
            logger.error("sendMessage request error: %s", exc)
            return False

    def send_status(self, message: str) -> None:
        """Optional operational messages (startup, errors)."""
        self._send_message(f"<i>{self._escape(message)}</i>")

    def send_ai_followup(
        self, item: MarketplaceItem, score: int | None, summary: str
    ) -> bool:
        """Second Telegram message after fast alert (OpenAI after alert)."""
        parts = [f"<b>🤖 AI — {self._escape(item.title[:80])}</b>"]
        if score is not None:
            parts.append(f"🎯 Snipe {score}/10")
        if summary:
            parts.append(self._escape(summary))
        parts.append(f'<a href="{html.escape(item.url, quote=True)}">Listing</a>')
        return self._send_message("\n".join(parts))
