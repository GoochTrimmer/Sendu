"""Carousell adapter (one class, one marketplace code per country).

VERIFIED 2026-11-04 in a real browser session (anonymous, no cookies, Singapore connection):
  * URL: GET https://<host>/search/<query>?sort_by=3   (sort_by=3 = "Recent")
  * The page is server-rendered. The results are embedded as JSON in the
    <script type="application/json"> that contains "SearchListing";
    cards are at .SearchListing.listingCards (~48 per page):
      id / listingID, title, price ("PHP 1,280", "RM39.90", "Rp 110,700", "S$110", "HK$1,080"),
      originalPrice.value (only after a price drop), condition, status "L",
      seller{id, username, sellerType e.g. "Professional Seller"},
      media[].photoItem.url, thumbnailURL,
      aboveFold[]: {component: time_created | active_bump | expired_bump,
                    timestampContent: {seconds: {low: <unix>}}}
  * Ordering: by that aboveFold time, newest first. For bumped listings it's the bump time
    (often a price drop), so it is the sort time; listed_at is only known for time_created.
  * Placeholder/banner cards have id "0" (e.g. title "spx_bp_delivery"): skipped.
  * Listing URL: https://<host>/p/<slug>-<id>/ (slug = title, lowercased, non-alphanumerics -> "-").
  * Paging needs a session token, so we read page 1 only: even Indonesia's 48 newest listings
    span ~36 h, far longer than the 30-min poll interval.

STILL TO CHECK: whether Carousell (Cloudflare) answers GitHub's runners. A plain `curl` from a
home connection got HTTP 403. Run the `probe` workflow per country. If blocked, don't work
around it: the options are running the worker from a home machine or dropping that country.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx

from ..models import Listing, SearchPage
from .base import browser_headers, check_response, parse_time, to_decimal


@dataclass(frozen=True)
class Country:
    code: str
    host: str
    currency: str
    accept_language: str


COUNTRIES: dict[str, Country] = {
    "carousell_ph": Country("carousell_ph", "www.carousell.ph", "PHP", "en-PH,en;q=0.9"),
    "carousell_my": Country("carousell_my", "www.carousell.com.my", "MYR", "en-MY,en;q=0.9,ms;q=0.8"),
    "carousell_id": Country("carousell_id", "id.carousell.com", "IDR", "id-ID,id;q=0.9,en;q=0.8"),
    "carousell_sg": Country("carousell_sg", "www.carousell.sg", "SGD", "en-SG,en;q=0.9"),
    "carousell_hk": Country("carousell_hk", "www.carousell.com.hk", "HKD", "en-HK,en;q=0.9,zh-HK;q=0.8"),
}

_JSON_SCRIPT = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SLUG_JUNK = re.compile(r"[^a-z0-9]+")


class CarousellParseError(Exception):
    """The page came back but didn't contain the expected results JSON."""


def extract_state(html: str) -> dict[str, Any]:
    """The embedded app-state JSON holding the search results."""
    for m in _JSON_SCRIPT.finditer(html):
        body = m.group(1)
        if '"SearchListing"' in body:
            return json.loads(body)
    raise CarousellParseError("no SearchListing JSON on the search page")


def parse_price(text: Any) -> Decimal | None:
    """'PHP 1,280' / 'RM39.90' / 'Rp 110,700' / 'S$110' / 'HK$1,080' -> Decimal; else None."""
    m = _NUMBER.search(str(text)) if text is not None else None
    return to_decimal(m.group(0).replace(",", "")) if m else None


def slugify(title: str) -> str:
    return _SLUG_JUNK.sub("-", title.lower()).strip("-")


def _card_time(card: dict[str, Any]) -> tuple[Any, str | None]:
    """(time, component) from the card's aboveFold timestamp."""
    for f in card.get("aboveFold") or []:
        ts = (f.get("timestampContent") or {}).get("seconds")
        if isinstance(ts, dict):
            ts = ts.get("low")
        t = parse_time(ts)
        if t is not None:
            return t, f.get("component")
    return None, None


class CarousellAdapter:
    def __init__(self, code: str):
        self.country = COUNTRIES[code]
        self.code = code

    def search_url(self, query: str) -> str:
        return f"https://{self.country.host}/search/{quote(query, safe='')}"

    async def search(
        self, client: httpx.AsyncClient, query: str, cursor: str | None = None
    ) -> SearchPage:
        headers = {
            **browser_headers(self.country.accept_language),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        resp = await client.get(self.search_url(query), params={"sort_by": "3"}, headers=headers)
        check_response(self, resp)
        return self.parse_html(resp.text)

    def is_blocked_response(self, resp: httpx.Response) -> bool:
        # Carousell sits behind Cloudflare: a challenge is a 403/503 or a 200 page without the
        # results JSON. (Normal pages may reference Cloudflare scripts, so don't scan for markers.)
        if resp.status_code == 503 and "cloudflare" in resp.headers.get("server", "").lower():
            return True
        return resp.status_code == 200 and '"SearchListing"' not in resp.text

    def parse_html(self, html: str) -> SearchPage:
        return self.parse(extract_state(html))

    def parse(self, state: dict[str, Any]) -> SearchPage:
        try:
            cards = state["SearchListing"]["listingCards"] or []
        except (KeyError, TypeError) as e:
            raise CarousellParseError("SearchListing.listingCards missing (page layout changed?)") from e
        host, listings = self.country.host, []
        for c in cards:
            lid = str(c.get("id") or c.get("listingID") or "")
            if not lid or lid == "0":
                continue  # banner / placeholder card
            title = c.get("title") or ""
            when, component = _card_time(c)
            price = parse_price(c.get("price"))
            seller = c.get("seller") or {}
            images = [
                (m.get("photoItem") or {}).get("url") for m in (c.get("media") or [])
            ] or [c.get("thumbnailURL")]
            slug = slugify(title)
            listings.append(
                Listing(
                    marketplace=self.code,
                    external_id=lid,
                    title=title,
                    price=price,
                    currency=self.country.currency,
                    seller_id=str(seller["id"]) if seller.get("id") else None,
                    seller_name=seller.get("username") or None,
                    url=f"https://{host}/p/{slug + '-' if slug else ''}{lid}/",
                    image_urls=[u for u in images if u],
                    listed_at=when if component == "time_created" else None,
                    sort_time=when,
                    raw_status="active" if c.get("status") in (None, "L") else "sold",
                )
            )
        return SearchPage(listings=listings, next_cursor=None)

    @staticmethod
    def fixture_payload(html: str) -> dict[str, Any]:
        """Only the results block, for tests/fixtures/<code>_real.json."""
        return {"SearchListing": {"listingCards": extract_state(html)["SearchListing"]["listingCards"]}}
