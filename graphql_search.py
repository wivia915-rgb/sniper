"""
Facebook Marketplace GraphQL search capture, replay, and JSON parsing.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from models import MarketplaceItem

logger = logging.getLogger(__name__)

FB_ITEM_URL = "https://www.facebook.com/marketplace/item/{listing_id}/"
LISTING_ID_RE = re.compile(r"^\d{8,20}$")

# Headers Playwright sets automatically or that break replay.
_SKIP_REQUEST_HEADERS = frozenset(
    {"content-length", "host", "connection", "accept-encoding"}
)


@dataclass
class CapturedGraphQLRequest:
    url: str
    headers: Dict[str, str]
    post_data: str
    friendly_name: str = ""


def _normalize_headers(headers: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in headers.items():
        lk = key.lower()
        if lk in _SKIP_REQUEST_HEADERS:
            continue
        out[key] = value
    return out


def is_marketplace_search_request(post_data: Optional[str], headers: Dict[str, str]) -> bool:
    body = (post_data or "").lower()
    friendly = headers.get("x-fb-friendly-name", "") or headers.get(
        "X-FB-Friendly-Name", ""
    )
    friendly_l = friendly.lower()
    if "marketplace" not in body and "marketplace" not in friendly_l:
        return False
    markers = (
        "marketplace_search",
        "marketplace_feed",
        "feed_unit",
        "search",
        "listing",
        "commerce",
    )
    return any(m in body or m in friendly_l for m in markers)


def is_marketplace_search_response(text: str) -> bool:
    lower = text.lower()
    if "marketplace" not in lower:
        return False
    return "creation_time" in lower or "marketplace_listing" in lower or "listing" in lower


def capture_from_playwright_request(request) -> CapturedGraphQLRequest:
    headers = _normalize_headers(dict(request.headers))
    post_data = request.post_data or ""
    friendly = headers.get("x-fb-friendly-name", headers.get("X-FB-Friendly-Name", ""))
    return CapturedGraphQLRequest(
        url=request.url,
        headers=headers,
        post_data=post_data,
        friendly_name=friendly,
    )


def _parse_json_chunks(raw: str) -> List[Any]:
    chunks: List[Any] = []
    text = raw.strip()
    if not text:
        return chunks
    try:
        chunks.append(json.loads(text))
        return chunks
    except json.JSONDecodeError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            chunks.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return chunks


def _creation_time_unix(node: Dict[str, Any]) -> Optional[int]:
    for key in ("creation_time", "creationTime", "listed_time"):
        val = node.get(key)
        if isinstance(val, (int, float)) and val > 0:
            ts = int(val)
            if ts > 10_000_000_000:
                ts //= 1000
            return ts
    obj = node.get("creation_time_object") or node.get("creationTimeObject")
    if isinstance(obj, dict):
        sec = obj.get("seconds")
        if isinstance(sec, (int, float)):
            return int(sec)
    return None


def _listing_id_from_node(node: Dict[str, Any]) -> Optional[str]:
    for key in (
        "listing_id",
        "listingId",
        "marketplace_listing_id",
        "id",
        "entity_id",
    ):
        val = node.get(key)
        if val is None:
            continue
        sid = str(val).strip()
        if LISTING_ID_RE.match(sid):
            return sid
        if sid.isdigit() and len(sid) >= 8:
            return sid
    return None


def _title_from_node(node: Dict[str, Any]) -> str:
    for key in (
        "marketplace_listing_title",
        "custom_title",
        "title",
        "name",
    ):
        val = node.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return "Untitled listing"


def _price_from_node(node: Dict[str, Any]) -> str:
    price = node.get("listing_price") or node.get("formatted_price")
    if isinstance(price, dict):
        for key in ("formatted_amount", "amount_with_offset_in_currency", "amount"):
            val = price.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
    if isinstance(price, str) and price.strip():
        return price.strip()
    amt = node.get("formatted_amount") or node.get("price")
    if amt is not None:
        return str(amt).strip()
    return "Price not listed"


def _location_from_node(node: Dict[str, Any]) -> str:
    loc = node.get("location") or node.get("reverse_geocode")
    if isinstance(loc, dict):
        for key in ("city", "display_name", "city_page"):
            val = loc.get(key)
            if isinstance(val, dict):
                name = val.get("display_name") or val.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
            if isinstance(val, str) and val.strip():
                return val.strip()
    return "Unknown"


def _image_from_node(node: Dict[str, Any]) -> str:
    for key in ("primary_listing_photo", "listing_photo", "image", "photo"):
        photo = node.get(key)
        if isinstance(photo, dict):
            image = photo.get("image") or photo
            if isinstance(image, dict):
                uri = image.get("uri") or image.get("url")
                if isinstance(uri, str):
                    return uri
            uri = photo.get("uri") or photo.get("url")
            if isinstance(uri, str):
                return uri
    return ""


def _looks_like_listing_node(node: Dict[str, Any]) -> bool:
    listing_id = _listing_id_from_node(node)
    if not listing_id:
        return False
    if _creation_time_unix(node) is not None:
        return True
    typename = str(node.get("__typename", "")).lower()
    return "listing" in typename or "marketplace" in typename


def _walk_listing_nodes(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, dict):
        if _looks_like_listing_node(obj):
            yield obj
        for value in obj.values():
            yield from _walk_listing_nodes(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_listing_nodes(item)


def _node_to_item(node: Dict[str, Any]) -> Optional[MarketplaceItem]:
    listing_id = _listing_id_from_node(node)
    if not listing_id:
        return None
    created = _creation_time_unix(node)
    time_posted = ""
    if created:
        age_min = max(0.0, (time.time() - created) / 60.0)
        time_posted = f"{age_min:.1f}m ago (graphql)"
    return MarketplaceItem(
        listing_id=listing_id,
        title=_title_from_node(node),
        price=_price_from_node(node),
        location=_location_from_node(node),
        url=FB_ITEM_URL.format(listing_id=listing_id),
        image_url=_image_from_node(node),
        time_posted=time_posted,
        creation_time_unix=created,
    )


def parse_marketplace_listings_from_response(
    raw: str,
    *,
    limit: int = 5,
) -> List[MarketplaceItem]:
    """Extract marketplace listings from a GraphQL HTTP response body."""
    items: List[MarketplaceItem] = []
    seen: Set[str] = set()

    for chunk in _parse_json_chunks(raw):
        for node in _walk_listing_nodes(chunk):
            item = _node_to_item(node)
            if not item or item.listing_id in seen:
                continue
            seen.add(item.listing_id)
            items.append(item)

    items.sort(key=lambda x: x.creation_time_unix or 0, reverse=True)
    return items[:limit]
