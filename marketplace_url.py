"""Facebook Marketplace URL helpers."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

MARKETPLACE_SORT_NEWEST = "creation_time_descend"


def ensure_newest_first_sort(search_url: str) -> str:
    """Append or replace sortBy=creation_time_descend on Marketplace search URLs."""
    parsed = urlparse(search_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["sortBy"] = [MARKETPLACE_SORT_NEWEST]
    query = urlencode(params, doseq=True)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment)
    )
