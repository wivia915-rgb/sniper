"""Scan speed/accuracy tuning from sniper settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sniper_config import SniperSettings


@dataclass(frozen=True)
class ScanTuning:
    feed_wait_ms: int
    between_feeds_sec: float
    keywords_per_cycle: int
    priority_feeds: int
    max_detail_checks: int
    stale_card_streak: int
    block_heavy_resources: bool


_NORMAL = ScanTuning(
    feed_wait_ms=1800,
    between_feeds_sec=0.35,
    keywords_per_cycle=14,
    priority_feeds=5,
    max_detail_checks=5,
    stale_card_streak=5,
    block_heavy_resources=True,
)

_TURBO = ScanTuning(
    feed_wait_ms=1100,
    between_feeds_sec=0.12,
    keywords_per_cycle=22,
    priority_feeds=8,
    max_detail_checks=5,
    stale_card_streak=4,
    block_heavy_resources=True,
)


def scan_tuning_from_settings(settings: SniperSettings) -> ScanTuning:
    if settings.get("turbo_mode"):
        return _TURBO
    return _NORMAL
