"""Optional OpenAI scoring for Marketplace moto listings (Morocco sniper)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from models import MarketplaceItem

logger = logging.getLogger(__name__)

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"

_SYSTEM_PROMPT = """\
Tu es un expert moto/scooter au Maroc (Facebook Marketplace, prix en MAD/DH).
Analyse l'annonce et réponds UNIQUEMENT en JSON valide (pas de markdown).
Critères snipe_score (1-10):
- 8-10: moto/scooter clair, prix suspect bas ou très bonne affaire, peu de red flags
- 5-7: moto plausible, prix normal ou info insuffisante
- 1-4: voiture/vélo/pièce/accessoire, arnaque probable, titre trompeur, prix absurde

Champs JSON:
- is_motorcycle (bool): vrai scooter/moto/quad/triporteur moto, pas voiture
- snipe_score (int 1-10)
- summary (string): 1 phrase FR ou Darija latin max 120 caractères pour Telegram
- skip_reason (string|null): si is_motorcycle false ou arnaque, courte raison; sinon null
"""


class OpenAIListingAnalyzer:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        min_snipe_score: int = 5,
        strict_motorcycle: bool = True,
        timeout: int = 25,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self._min_score = max(1, min(10, int(min_snipe_score)))
        self._strict_moto = bool(strict_motorcycle)
        self._timeout = timeout
        self.last_error: str = ""

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _user_payload(self, item: MarketplaceItem) -> str:
        return json.dumps(
            {
                "title": item.title,
                "price": item.price,
                "location": item.location,
                "time_posted": item.time_posted,
                "url": item.url,
            },
            ensure_ascii=False,
        )

    def _parse_response_text(self, raw: str) -> dict[str, Any] | None:
        text = (raw or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    def analyze(self, item: MarketplaceItem) -> tuple[bool, str, int | None]:
        """
        Returns (should_alert, summary_for_telegram, snipe_score).
        On API/parse errors, allows alert (fail-open) with empty summary.
        """
        self.last_error = ""
        if not self.is_configured():
            self.last_error = "OpenAI API key missing"
            return True, "", None

        body = {
            "model": self._model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Annonce Marketplace:\n"
                        f"{self._user_payload(item)}\n"
                        "Réponds avec is_motorcycle, snipe_score, summary, skip_reason."
                    ),
                },
            ],
        }
        try:
            resp = requests.post(
                OPENAI_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            self.last_error = str(exc)
            logger.warning("OpenAI request failed: %s", exc)
            return True, "", None

        if resp.status_code != 200:
            self.last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            logger.warning("OpenAI error: %s", self.last_error)
            return True, "", None

        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            self.last_error = f"Bad OpenAI response: {exc}"
            return True, "", None

        data = self._parse_response_text(content)
        if not data:
            self.last_error = "Could not parse OpenAI JSON"
            return True, "", None

        is_moto = bool(data.get("is_motorcycle"))
        score_raw = data.get("snipe_score", 5)
        try:
            score = int(score_raw)
        except (TypeError, ValueError):
            score = 5
        score = max(1, min(10, score))
        summary = str(data.get("summary") or "").strip()[:200]
        skip_reason = data.get("skip_reason")
        skip_text = str(skip_reason).strip() if skip_reason else ""

        if self._strict_moto and not is_moto:
            reason = skip_text or "pas une moto/scooter"
            self.last_error = reason
            return False, summary, score

        if score < self._min_score:
            reason = skip_text or f"score {score} < min {self._min_score}"
            self.last_error = reason
            return False, summary, score

        return True, summary, score

    def send_test(self) -> tuple[bool, str]:
        """Quick connectivity test (minimal tokens)."""
        if not self.is_configured():
            return False, "API key missing"
        body = {
            "model": self._model,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "Reply with OK only."}],
        }
        try:
            resp = requests.post(
                OPENAI_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            return False, str(exc)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        return True, f"OpenAI OK ({self._model})"
