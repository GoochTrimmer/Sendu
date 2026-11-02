"""Yahoo! Auctions JP adapter: the public search results page.

VERIFIED 2026-11-02 in a real browser session (anonymous search page, Singapore connection):
  * URL: GET https://auctions.yahoo.co.jp/search/search?p=<query>&s1=new&o1=d&n=50&b=<offset>
    s1=new&o1=d = newest first (metadata.sort "-FIRST_START_TIME"); n = page size (50);
    b = 1-based offset (page 2 is b=51). Includes auctions AND Buy It Now (fixed price).
  * The page is server-rendered Next.js. Results are in the <script id="__NEXT_DATA__"> JSON at
    props.pageProps.initialState.search.items.listing:
      items[]: auctionId, title, price (current, JPY), buyNowPrice, bidCount, isFixedPrice,
               startTime / endTime (ISO-8601 +09:00), imageUrl, itemCondition, isFeatured,
               seller{userId (opaque 29-char id), displayName (masked "********"), isStore}
      totalResultsAvailable, metadata{limit, sort, exceptSuspectedFake: true}
  * Ordering: strictly by startTime, except `isFeatured` promoted items, which are skipped.
  * Listing URL: https://auctions.yahoo.co.jp/jp/auction/{auctionId}
  * One plain GET with normal browser headers; no login, cookies or tokens.

STILL TO CHECK (README 6.3): Yahoo! JAPAN blocks some regions (e.g. EEA). Run the `probe`
workflow to see whether GitHub's US runners get results. If they don't, the planned fallback
is a Buyee adapter (user decision 2026-11-02); don't work around the block.
tests/fixtures/yahoo_auctions_search.json mirrors the verified shape; capture a real page with
`python -m sourcer probe yahoo_auctions "ヨウジヤマモト" --save`.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ..models import Listing, SearchPage
from .base import browser_headers, check_response, looks_like_challenge, parse_time, to_decimal

SEARCH_URL = "https://auctions.yahoo.co.jp/search/search"
PAGE_SIZE = 50
_NEXT_DATA = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)


class YahooParseError(Exception):
    """The page came back but didn't contain the expected results JSON."""


def extract_next_data(html: str) -> dict[str, Any]:
    m = _NEXT_DATA.search(html)
    if not m:
        raise YahooParseError("no __NEXT_DATA__ script on the search page")
    return json.loads(m.group(1))


def _listing_block(next_data: dict[str, Any]) -> dict[str, Any]:
    props = next_data.get("props") or {}
    for state in ((props.get("pageProps") or {}).get("initialState"), props.get("initialState")):
        try:
            return state["search"]["items"]["listing"]
        except (KeyError, TypeError):
            continue
    raise YahooParseError("search results not found in __NEXT_DATA__ (page layout changed?)")


class YahooAuctionsAdapter:
    code = "yahoo_auctions"

    def build_params(self, query: str, cursor: str | None) -> dict[str, str]:
        return {"p": query, "s1": "new", "o1": "d", "n": str(PAGE_SIZE), "b": cursor or "1"}

    async def search(
        self, client: httpx.AsyncClient, query: str, cursor: str | None = None
    ) -> SearchPage:
        headers = {
            **browser_headers("ja-JP,ja;q=0.9,en;q=0.8"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        resp = await client.get(SEARCH_URL, params=self.build_params(query, cursor), headers=headers)
        check_response(self, resp)
        return self.parse_html(resp.text, offset=int(cursor or 1))

    def is_blocked_response(self, resp: httpx.Response) -> bool:
        # A region block or challenge serves an HTML page without the results JSON.
        if looks_like_challenge(resp):
            return True
        return resp.status_code == 200 and "__NEXT_DATA__" not in resp.text[:2_000_000]

    @classmethod
    def parse_html(cls, html: str, offset: int = 1) -> SearchPage:
        return cls.parse(extract_next_data(html), offset=offset)

    @staticmethod
    def parse(next_data: dict[str, Any], offset: int = 1) -> SearchPage:
        block = _listing_block(next_data)
        listings: list[Listing] = []
        for it in block.get("items") or []:
            aid = it.get("auctionId")
            if not aid or it.get("isFeatured"):
                continue  # promoted items break the newest-first order
            seller = it.get("seller") or {}
            name = seller.get("displayName")
            started = parse_time(it.get("startTime"))
            price = to_decimal(it.get("price"))
            if price is None or price == 0:
                price = to_decimal(it.get("buyNowPrice"))
            listings.append(
                Listing(
                    marketplace="yahoo_auctions",
                    external_id=str(aid),
                    title=it.get("title") or "",
                    description=None,
                    price=price,
                    currency="JPY",
                    size=None,
                    brand=None,
                    seller_id=seller.get("userId") or None,
                    seller_name=None if not name or set(name) == {"*"} else name,
                    url=f"https://auctions.yahoo.co.jp/jp/auction/{aid}",
                    image_urls=[it["imageUrl"]] if it.get("imageUrl") else [],
                    listed_at=started,
                    sort_time=started,
                    raw_status="active",
                )
            )
        total = int(block.get("totalResultsAvailable") or 0)
        next_offset = offset + PAGE_SIZE
        has_items = bool(block.get("items"))
        next_cursor = str(next_offset) if has_items and next_offset <= total else None
        return SearchPage(listings=listings, next_cursor=next_cursor)
