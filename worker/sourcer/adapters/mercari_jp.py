"""Mercari Japan adapter: the JSON search API that jp.mercari.com's own frontend calls.

VERIFIED 2026-11-02 in a real browser session (anonymous search page, Singapore connection):
  * Endpoint: POST https://api.mercari.jp/v2/entities:search -> 200 JSON.
  * Headers sent by the frontend: Accept, Accept-Language, Content-Type, X-Platform: web,
    X-Country-Code, DPoP. The DPoP proof is an ES256 JWT (header typ=dpop+jwt + public JWK
    crv/kty/x/y; claims iat/jti/htu/htm/uuid) made with a key the browser generates itself.
    No login or cookies are involved; we do exactly the same.
  * Body: searchCondition.sort=SORT_CREATED_TIME, order=ORDER_DESC, status=[STATUS_ON_SALE],
    pageSize=120, pageToken, searchSessionId, laplaceDeviceUuid, withAuction=true, ...
  * Response: {meta:{nextPageToken, numFound}, items:[...]}. Item fields used: id, sellerId,
    status, name, price (string JPY), created / updated (unix seconds strings), thumbnails,
    photos[].uri, itemType (ITEM_TYPE_MERCARI | ITEM_TYPE_BEYOND = Mercari Shops),
    itemBrand{name}, itemSize{name} / itemSizes[], auction{bidDeadline, highestBid} or null.
  * IMPORTANT: "newest" actually orders by `updated`, not `created`: price cuts and edits push
    old items back to the top (seen: created Dec 2025, updated today, at position 1). So
    listed_at = created (true listing time) and sort_time = updated. The worker uses
    sort_time for the paging stop rule and flags re-surfaced items as bumped, not new.
  * Seller ID: items[].sellerId (also present for Shops items).
  * Shops items: URL https://jp.mercari.com/shops/product/{id}; regular: /item/{id}.

STILL TO CHECK (README 6.3): does it answer GitHub Actions runners (US)? Run the `probe` workflow.
tests/fixtures/mercari_jp_search.json mirrors the verified shape; replace it with a real
capture via `python -m sourcer probe mercari_jp "ヨウジヤマモト" --save`.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from ..models import Listing, SearchPage
from .base import browser_headers, check_response, looks_like_challenge, parse_time, to_decimal

SEARCH_URL = "https://api.mercari.jp/v2/entities:search"
PAGE_SIZE = 120


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class DPoPSigner:
    """Ephemeral ES256 key, like the browser frontend generates per session."""

    def __init__(self) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())
        nums = self._key.public_key().public_numbers()
        self.jwk = {
            "crv": "P-256",
            "kty": "EC",
            "x": _b64url(nums.x.to_bytes(32, "big")),
            "y": _b64url(nums.y.to_bytes(32, "big")),
        }
        self.session_uuid = str(uuid.uuid4())

    def proof(self, method: str, url: str) -> str:
        header = {"typ": "dpop+jwt", "alg": "ES256", "jwk": self.jwk}
        payload = {
            "iat": int(time.time()),
            "jti": str(uuid.uuid4()),
            "htu": url,
            "htm": method.upper(),
            "uuid": self.session_uuid,
        }
        signing_input = (
            _b64url(json.dumps(header, separators=(",", ":")).encode())
            + "."
            + _b64url(json.dumps(payload, separators=(",", ":")).encode())
        )
        der = self._key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return signing_input + "." + _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


class MercariJPAdapter:
    code = "mercari_jp"

    def __init__(self) -> None:
        self._signer = DPoPSigner()
        self._session_id = uuid.uuid4().hex
        self._device_uuid = str(uuid.uuid4())

    def build_body(self, query: str, cursor: str | None) -> dict[str, Any]:
        # Mirrors the body the web frontend sent (verified 2026-11-02), minus UI-only toggles.
        return {
            "userId": "",
            "pageSize": PAGE_SIZE,
            "pageToken": cursor or "",
            "searchSessionId": self._session_id,
            "laplaceDeviceUuid": self._device_uuid,
            "source": "BaseSerp",
            "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
            "thumbnailTypes": [],
            "searchCondition": {
                "keyword": query,
                "excludeKeyword": "",
                "sort": "SORT_CREATED_TIME",
                "order": "ORDER_DESC",
                "status": ["STATUS_ON_SALE"],
                "sizeId": [],
                "categoryId": [],
                "brandId": [],
                "sellerId": [],
                "shopIds": [],
                "priceMin": 0,
                "priceMax": 0,
                "itemConditionId": [],
                "shippingPayerId": [],
                "shippingFromArea": [],
                "shippingMethod": [],
                "excludeShippingMethodIds": [],
                "colorId": [],
                "hasCoupon": False,
                "attributes": [],
                "itemTypes": [],
                "skuIds": [],
            },
            "serviceFrom": "suruga",
            "withAuction": True,
            "withItemBrand": True,
            "withItemSize": False,
            "withItemSizes": True,
            "withItemPromotions": False,
            "withShopname": False,
        }

    async def search(
        self, client: httpx.AsyncClient, query: str, cursor: str | None = None
    ) -> SearchPage:
        headers = {
            **browser_headers("ja-JP,ja;q=0.9,en;q=0.8"),
            "Content-Type": "application/json",
            "X-Platform": "web",
            "DPoP": self._signer.proof("POST", SEARCH_URL),
            "Origin": "https://jp.mercari.com",
            "Referer": "https://jp.mercari.com/",
        }
        resp = await client.post(SEARCH_URL, json=self.build_body(query, cursor), headers=headers)
        check_response(self, resp)
        return self.parse(resp.json())

    def is_blocked_response(self, resp: httpx.Response) -> bool:
        # An HTML page instead of JSON (geo-block, challenge) counts as blocked.
        return looks_like_challenge(resp) or (
            resp.status_code == 200 and "json" not in resp.headers.get("content-type", "json")
        )

    @staticmethod
    def parse(body: dict[str, Any]) -> SearchPage:
        listings: list[Listing] = []
        for it in body.get("items") or []:
            iid = it.get("id")
            if not iid:
                continue
            is_shop = it.get("itemType") == "ITEM_TYPE_BEYOND"
            url = (
                f"https://jp.mercari.com/shops/product/{iid}"
                if is_shop
                else f"https://jp.mercari.com/item/{iid}"
            )
            brand = (it.get("itemBrand") or {}).get("name")
            size = (it.get("itemSize") or {}).get("name")
            if not size and it.get("itemSizes"):
                size = (it["itemSizes"][0] or {}).get("name")
            status_raw = (it.get("status") or "").upper()
            status = (
                "sold"
                if "SOLD" in status_raw or "TRADING" in status_raw
                else "active"
                if "ON_SALE" in status_raw or not status_raw
                else "removed"
            )
            # Shops items report sellerId "0" (seen live 2026-11-02); "0" would make one block
            # hit every shop, so use the shop's own id instead.
            raw_seller = str(it.get("sellerId") or "")
            shop_id = (it.get("shop") or {}).get("id") if isinstance(it.get("shop"), dict) else None
            if raw_seller in ("", "0"):
                seller_id = f"shop:{shop_id}" if shop_id else None
            else:
                seller_id = raw_seller
            created = parse_time(it.get("created"))
            photos = [p.get("uri") for p in it.get("photos") or [] if isinstance(p, dict)]
            images = [u for u in photos if u] or list(it.get("thumbnails") or [])
            listings.append(
                Listing(
                    marketplace="mercari_jp",
                    external_id=str(iid),
                    title=it.get("name") or "",
                    description=None,
                    price=to_decimal(it.get("price")),
                    currency="JPY",
                    size=size,
                    brand=brand,
                    seller_id=str(seller_id) if seller_id else None,
                    seller_name=(it.get("shopName") or None) if is_shop else None,
                    url=url,
                    image_urls=images,
                    listed_at=created,
                    sort_time=max(filter(None, [created, parse_time(it.get("updated"))]), default=None),
                    raw_status=status,
                )
            )
        meta = body.get("meta") or {}
        return SearchPage(listings=listings, next_cursor=meta.get("nextPageToken") or None)
