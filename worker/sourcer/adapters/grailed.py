"""Grailed adapter: the Algolia search index that grailed.com's own frontend queries.

VERIFIED 2026-11-02 in a real browser session:
  * Endpoint: POST https://mnrwefss2q-dsn.algolia.net/1/indexes/*/queries (multi-query form;
    the single-index form /1/indexes/{INDEX}/query used here is equivalent Algolia API).
    Headers: x-algolia-application-id (MNRWEFSS2Q), x-algolia-api-key.
  * Sort dropdown -> index: "Newly Listed" = Listing_by_date_added_production (our default),
    "Latest Activity" = Listing_production (includes bumps), "Most Relevant" =
    Listing_by_listing_quality_production.
  * Params: query, hitsPerPage=40, page (0-based), numericFilters on price_i.
  * Hit fields used: id (int), title, price_i, size, designer_names, user{id (int), username},
    cover_photo{url, image_url}, created_at, bumped_at, sold, deleted. No description.
    On the date_added index results are ordered by created_at, so listed_at = sort_time =
    created_at (bumps don't re-surface on this index).

OPEN ISSUE: the API key. The frontend obtains it from https://www.grailed.com/api/algolia/keys,
which returned 404 to an anonymous (cookie-less) request, and the key isn't in the anonymous
page HTML. The browser used for the check was signed in to Grailed, so it's not yet known
whether logged-out visitors get a key some other way. Per README 4.3/6.3 we never use account
credentials: until a logged-out key is confirmed, GRAILED_ALGOLIA_API_KEY must be left unset
(the adapter then reports "not configured"). See README "Grailed" note.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from ..config import Settings
from ..models import Listing, SearchPage
from .base import (
    AdapterNotConfigured,
    browser_headers,
    check_response,
    looks_like_challenge,
    parse_time,
    to_decimal,
)

PAGE_SIZE = 40


class GrailedAdapter:
    code = "grailed"

    def __init__(self, settings: Settings):
        self.app_id = settings.grailed_algolia_app_id
        self.api_key = settings.grailed_algolia_api_key
        self.index = settings.grailed_algolia_index

    async def search(
        self, client: httpx.AsyncClient, query: str, cursor: str | None = None
    ) -> SearchPage:
        if not self.api_key:
            raise AdapterNotConfigured("GRAILED_ALGOLIA_API_KEY not set (see README Setup step 6)")
        page = int(cursor or 0)
        url = f"https://{self.app_id.lower()}-dsn.algolia.net/1/indexes/{self.index}/query"
        headers = {
            **browser_headers(),
            "X-Algolia-Application-Id": self.app_id,
            "X-Algolia-API-Key": self.api_key,
            "Origin": "https://www.grailed.com",
            "Referer": "https://www.grailed.com/",
        }
        params = urlencode({"query": query, "hitsPerPage": PAGE_SIZE, "page": page})
        resp = await client.post(url, json={"params": params}, headers=headers)
        check_response(self, resp)
        return self.parse(resp.json())

    def is_blocked_response(self, resp: httpx.Response) -> bool:
        return looks_like_challenge(resp)

    @staticmethod
    def parse(body: dict[str, Any]) -> SearchPage:
        listings: list[Listing] = []
        for h in body.get("hits") or []:
            lid = h.get("id") or h.get("objectID")
            if lid is None:
                continue
            user = h.get("user") or {}
            designers = h.get("designers") or []
            brand = h.get("designer_names") or ", ".join(
                d.get("name", "") for d in designers if isinstance(d, dict)
            )
            photos = [p.get("url") for p in h.get("photos") or [] if isinstance(p, dict)]
            cover_obj = h.get("cover_photo") or {}
            cover = cover_obj.get("url") or cover_obj.get("image_url")
            images = ([cover] if cover else []) + [p for p in photos if p and p != cover]
            status = "sold" if h.get("sold") else "removed" if h.get("deleted") else "active"
            listings.append(
                Listing(
                    marketplace="grailed",
                    external_id=str(lid),
                    title=h.get("title") or "",
                    description=h.get("description"),
                    price=to_decimal(h.get("price_i") or h.get("price")),
                    currency=h.get("currency") or "USD",
                    size=str(h["size"]) if h.get("size") not in (None, "") else None,
                    brand=brand or None,
                    seller_id=str(user["id"]) if user.get("id") is not None else None,
                    seller_name=user.get("username"),
                    url=f"https://www.grailed.com/listings/{lid}",
                    image_urls=images,
                    listed_at=parse_time(h.get("created_at")),
                    sort_time=parse_time(h.get("created_at")),
                    raw_status=status,
                )
            )
        page, nb_pages = int(body.get("page", 0)), int(body.get("nbPages", 0))
        next_cursor = str(page + 1) if page + 1 < nb_pages else None
        return SearchPage(listings=listings, next_cursor=next_cursor)
