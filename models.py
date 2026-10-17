"""Shared data models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MarketplaceItem:
    listing_id: str
    title: str
    price: str
    location: str
    url: str
    image_url: str
    time_posted: str
    creation_time_unix: int | None = None
