"""eBay adapter: official Browse API.

Endpoint research (from eBay's public developer docs; needs re-checking with live keys):
  * Token:   POST https://api.ebay.com/identity/v1/oauth2/token
             grant_type=client_credentials, scope=https://api.ebay.com/oauth/api_scope
             HTTP Basic auth with EBAY_CLIENT_ID:EBAY_CLIENT_SECRET. Token lasts about 2 h.
  * Search:  GET https://api.ebay.com/buy/browse/v1/item_summary/search
             q=<query>&sort=newlyListed&limit=50&offset=<n>
             Header X-EBAY-C-MARKETPLACE-ID picks the site (EBAY_US, EBAY_GB, ...).
  * Newest first: sort=newlyListed.
  * Paging: response `next` URL / `offset`+`limit`. We use the offset as the cursor.
  * Seller: itemSummaries[].seller.username (the summary has no numeric user ID,
    and eBay usernames are stable enough to use as the seller ID).
  * Listed at: itemSummaries[].itemCreationDate (ISO-8601).
  * Size isn't in item summaries; it's left empty.

STATUS: written against the documented schema. tests/fixtures/ebay_search.json is
SYNTHETIC; replace it with a real capture (`python -m sourcer probe ebay "yohji" --save`).
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from ..config import Settings
from ..models import Listing, SearchPage
from .base import AdapterNotConfigured, browser_headers, check_response, parse_time, to_decimal

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"
PAGE_SIZE = 50


class EbayAdapter:
    code = "ebay"

    def __init__(self, settings: Settings):
        self.client_id = settings.ebay_client_id
        self.client_secret = settings.ebay_client_secret
        self.marketplace_id = settings.ebay_marketplace_id
        self._token: str | None = None
        self._token_expires = 0.0

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        if not (self.client_id and self.client_secret):
            raise AdapterNotConfigured("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set")
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        resp = await client.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials", "scope": SCOPE},
            auth=(self.client_id, self.client_secret),
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires = time.time() + int(body.get("expires_in", 7200))
        return self._token

    async def search(
        self, client: httpx.AsyncClient, query: str, cursor: str | None = None
    ) -> SearchPage:
        token = await self._get_token(client)
        params = {"q": query, "sort": "newlyListed", "limit": str(PAGE_SIZE), "offset": cursor or "0"}
        headers = {
            **browser_headers(),
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
        }
        resp = await client.get(SEARCH_URL, params=params, headers=headers)
        check_response(self, resp)
        return self.parse(resp.json())

    def is_blocked_response(self, resp: httpx.Response) -> bool:
        return False  # official API: 429 is handled generically

    @staticmethod
    def parse(body: dict[str, Any]) -> SearchPage:
        listings: list[Listing] = []
        for it in body.get("itemSummaries") or []:
            item_id = it.get("itemId") or it.get("legacyItemId")
            url = it.get("itemWebUrl")
            if not item_id or not url:
                continue
            price = it.get("price") or {}
            images = [(it.get("image") or {}).get("imageUrl")]
            images += [i.get("imageUrl") for i in it.get("additionalImages") or []]
            seller = it.get("seller") or {}
            seller_key = seller.get("userId") or seller.get("username")
            listings.append(
                Listing(
                    marketplace="ebay",
                    external_id=str(item_id),
                    title=it.get("title") or "",
                    description=it.get("shortDescription"),
                    price=to_decimal(price.get("value")),
                    currency=price.get("currency"),
                    size=None,
                    brand=None,
                    seller_id=seller_key,
                    seller_name=seller.get("username"),
                    url=url,
                    image_urls=[u for u in images if u],
                    listed_at=parse_time(it.get("itemCreationDate")),
                    raw_status="active",
                )
            )
        next_cursor = None
        if body.get("next"):
            qs = parse_qs(urlparse(body["next"]).query)
            next_cursor = (qs.get("offset") or [None])[0]
            if next_cursor is None:
                next_cursor = str(int(body.get("offset", 0)) + int(body.get("limit", PAGE_SIZE)))
        return SearchPage(listings=listings, next_cursor=next_cursor)
