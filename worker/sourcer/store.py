"""All Supabase reads/writes.

`SupabaseStore` talks to PostgREST over httpx with the service-role key (bypasses
RLS). `MemoryStore` implements the same interface in memory; it's used for
--dry-run and tests. `DryRunStore` reads from a real store (if configured) and
keeps every write in memory.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from .models import (
    BlockedSeller,
    Listing,
    MarketplaceStatus,
    PendingMatch,
    Profile,
    SavedSearch,
    SearchOverride,
    SearchState,
    SeenRow,
)


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def listing_row(listing: Listing, now: datetime) -> dict[str, Any]:
    desc = listing.description
    return {
        "marketplace": listing.marketplace,
        "external_id": listing.external_id,
        "title": listing.title[:500],
        "description_snippet": desc[:500] if desc else None,
        "price": listing.price,
        "currency": listing.currency,
        "size": listing.size,
        "brand": listing.brand,
        "seller_id": listing.seller_id,
        "seller_name": listing.seller_name,
        "url": listing.url,
        "image_urls": listing.image_urls[:4],
        "listed_at": listing.listed_at,
        "status": listing.raw_status if listing.raw_status in ("active", "sold", "removed") else "active",
        "last_seen_at": now,
    }


def _in(values: list[str]) -> str:
    quoted = ",".join('"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"' for v in values)
    return f"in.({quoted})"


class SupabaseStore:
    def __init__(self, url: str, service_key: str, client: httpx.AsyncClient | None = None):
        root = url.strip().rstrip("/")
        if root.endswith("/rest/v1"):  # tolerate the REST URL being pasted instead of the project URL
            root = root[: -len("/rest/v1")]
        self.base = root + "/rest/v1"
        self.headers = {"apikey": service_key, "Content-Type": "application/json"}
        # Legacy service_role keys are JWTs and also go in Authorization. New-style secret keys
        # (sb_secret_...) are not JWTs: they must only be sent as `apikey`, and Supabase's
        # gateway turns them into a service-role session itself.
        if service_key.startswith("eyJ"):
            self.headers["Authorization"] = f"Bearer {service_key}"
        self.client = client or httpx.AsyncClient(timeout=30)

    async def aclose(self) -> None:
        await self.client.aclose()

    # -- low level ---------------------------------------------------------
    async def _get(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset, page = 0, 1000
        while True:
            r = await self.client.get(
                f"{self.base}/{table}",
                params=params,
                headers={**self.headers, "Range-Unit": "items", "Range": f"{offset}-{offset + page - 1}"},
            )
            r.raise_for_status()
            rows = r.json()
            out.extend(rows)
            if len(rows) < page:
                return out
            offset += page

    async def _post(
        self, table: str, rows: Any, prefer: str, params: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        if isinstance(rows, list) and not rows:
            return []
        r = await self.client.post(
            f"{self.base}/{table}",
            params=params or {},
            content=json.dumps(_jsonable(rows)),
            headers={**self.headers, "Prefer": prefer},
        )
        r.raise_for_status()
        return r.json() if r.content and "return=representation" in prefer else []

    async def _patch(self, table: str, filters: dict[str, str], fields: dict[str, Any]) -> None:
        r = await self.client.patch(
            f"{self.base}/{table}",
            params=filters,
            content=json.dumps(_jsonable(fields)),
            headers={**self.headers, "Prefer": "return=minimal"},
        )
        r.raise_for_status()

    # -- loading -----------------------------------------------------------
    async def load_searches(self) -> list[SavedSearch]:
        rows = await self._get(
            "saved_searches",
            {"select": "*,search_marketplace_overrides(*)", "is_paused": "eq.false"},
        )
        out = []
        for r in rows:
            overrides = {
                o["marketplace"]: SearchOverride(**o) for o in r.pop("search_marketplace_overrides") or []
            }
            out.append(SavedSearch(**r, overrides=overrides))
        return out

    async def load_states(self) -> dict[tuple[str, str], SearchState]:
        rows = await self._get("search_state", {"select": "search_id,marketplace,initialized,last_polled_at,consecutive_errors"})
        return {(r["search_id"], r["marketplace"]): SearchState(**r) for r in rows}

    async def load_marketplaces(self) -> dict[str, MarketplaceStatus]:
        rows = await self._get("marketplaces", {"select": "*"})
        return {r["code"]: MarketplaceStatus(**r) for r in rows}

    async def load_blocklist(self) -> list[BlockedSeller]:
        rows = await self._get("blocked_sellers", {"select": "marketplace,seller_id,seller_name,scope,owner_id"})
        return [BlockedSeller(**r) for r in rows]

    async def load_profiles(self) -> dict[str, Profile]:
        rows = await self._get("profiles", {"select": "id,display_name,telegram_chat_id,notify_mode"})
        return {r["id"]: Profile(**r) for r in rows}

    # -- dedup -------------------------------------------------------------
    async def get_seen(self, marketplace: str, ids: list[str]) -> dict[str, SeenRow]:
        if not ids:
            return {}
        rows = await self._get(
            "seen_listings",
            {"select": "*", "marketplace": f"eq.{marketplace}", "external_id": _in(ids)},
        )
        return {r["external_id"]: SeenRow(**r) for r in rows}

    async def insert_seen(self, rows: list[dict[str, Any]]) -> None:
        await self._post(
            "seen_listings", rows, "resolution=ignore-duplicates,return=minimal",
            {"on_conflict": "marketplace,external_id"},
        )

    async def touch_seen(self, rows: list[dict[str, Any]]) -> None:
        await self._post(
            "seen_listings", rows, "resolution=merge-duplicates,return=minimal",
            {"on_conflict": "marketplace,external_id"},
        )

    # -- listings / matches ------------------------------------------------
    async def get_listings(self, marketplace: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        rows = await self._get(
            "listings",
            {"select": "id,external_id,price,currency,listed_at,status", "marketplace": f"eq.{marketplace}", "external_id": _in(ids)},
        )
        return {r["external_id"]: r for r in rows}

    async def upsert_listing(self, row: dict[str, Any]) -> str:
        res = await self._post(
            "listings", row, "resolution=merge-duplicates,return=representation",
            {"on_conflict": "marketplace,external_id", "select": "id"},
        )
        return res[0]["id"]

    async def upsert_listings(self, rows: list[dict[str, Any]], chunk: int = 200) -> dict[str, str]:
        """Bulk upsert; returns {external_id: listing uuid}. Rows must share one marketplace."""
        out: dict[str, str] = {}
        for i in range(0, len(rows), chunk):
            res = await self._post(
                "listings", rows[i : i + chunk], "resolution=merge-duplicates,return=representation",
                {"on_conflict": "marketplace,external_id", "select": "id,external_id"},
            )
            out.update({r["external_id"]: r["id"] for r in res})
        return out

    async def update_listing(self, listing_id: str, fields: dict[str, Any]) -> None:
        await self._patch("listings", {"id": f"eq.{listing_id}"}, fields)

    async def insert_price_history(self, rows: list[dict[str, Any]]) -> None:
        for i in range(0, len(rows), 500):
            await self._post("price_history", rows[i : i + 500], "return=minimal")

    async def insert_matches(self, rows: list[dict[str, Any]]) -> None:
        for i in range(0, len(rows), 500):
            await self._post(
                "matches", rows[i : i + 500], "resolution=ignore-duplicates,return=minimal",
                {"on_conflict": "search_id,listing_id"},
            )

    async def save_state(self, search_id: str, marketplace: str, fields: dict[str, Any]) -> None:
        await self._post(
            "search_state", {"search_id": search_id, "marketplace": marketplace, **fields},
            "resolution=merge-duplicates,return=minimal", {"on_conflict": "search_id,marketplace"},
        )

    async def update_marketplace(self, code: str, fields: dict[str, Any]) -> None:
        await self._patch("marketplaces", {"code": f"eq.{code}"}, fields)

    async def update_heartbeat(self, fields: dict[str, Any]) -> None:
        await self._patch("worker_heartbeat", {"id": "eq.1"}, fields)

    # -- notifications -----------------------------------------------------
    async def pending_matches(self) -> list[PendingMatch]:
        rows = await self._get(
            "matches",
            {
                "select": "id,user_id,listing_id,search:saved_searches(title),listing:listings(*)",
                "notified_at": "is.null",
                "order": "matched_at.asc",
            },
        )
        out = []
        for r in rows:
            lr = r["listing"]
            if not lr:
                continue
            listing = Listing(
                marketplace=lr["marketplace"], external_id=lr["external_id"], title=lr["title"],
                description=lr.get("description_snippet"), price=lr.get("price"),
                currency=lr.get("currency"), size=lr.get("size"), brand=lr.get("brand"),
                seller_id=lr.get("seller_id"), seller_name=lr.get("seller_name"), url=lr["url"],
                image_urls=lr.get("image_urls") or [], listed_at=lr.get("listed_at"),
            )
            out.append(PendingMatch(
                match_id=r["id"], user_id=r["user_id"], listing_id=r["listing_id"],
                search_title=(r.get("search") or {}).get("title", ""), listing=listing,
            ))
        return out

    async def mark_notified(self, match_ids: list[str], at: datetime) -> None:
        for i in range(0, len(match_ids), 100):
            await self._patch("matches", {"id": _in(match_ids[i : i + 100])}, {"notified_at": at})

    async def link_telegram(self, code: str, chat_id: int) -> bool:
        r = await self.client.patch(
            f"{self.base}/profiles",
            params={"telegram_link_code": f"eq.{code}", "select": "id"},
            content=json.dumps({"telegram_chat_id": chat_id, "telegram_link_code": None}),
            headers={**self.headers, "Prefer": "return=representation"},
        )
        r.raise_for_status()
        return bool(r.json())

    async def prune(self) -> dict[str, Any]:
        r = await self.client.post(f"{self.base}/rpc/prune_storage", headers=self.headers, content="{}")
        r.raise_for_status()
        return r.json()


class MemoryStore:
    """In-memory store with the same interface. Used for dry runs and tests."""

    def __init__(
        self,
        searches: list[SavedSearch] | None = None,
        marketplaces: dict[str, MarketplaceStatus] | None = None,
        blocklist: list[BlockedSeller] | None = None,
        profiles: dict[str, Profile] | None = None,
    ):
        self.searches = searches or []
        self.states: dict[tuple[str, str], SearchState] = {}
        self.state_fields: dict[tuple[str, str], dict[str, Any]] = {}
        self.marketplaces = marketplaces or {
            c: MarketplaceStatus(code=c, name=c) for c in ("ebay", "grailed", "mercari_jp", "yahoo_auctions")
        }
        self.blocklist = blocklist or []
        self.profiles = profiles or {}
        self.seen: dict[tuple[str, str], SeenRow] = {}
        self.listings: dict[tuple[str, str], dict[str, Any]] = {}
        self.matches: list[dict[str, Any]] = []
        self.price_history: list[dict[str, Any]] = []
        self.heartbeat: dict[str, Any] = {}
        self.marketplace_updates: dict[str, dict[str, Any]] = {}
        self._next_id = 0

    async def aclose(self) -> None:
        pass

    async def load_searches(self) -> list[SavedSearch]:
        return [s for s in self.searches if not s.is_paused]

    async def load_states(self) -> dict[tuple[str, str], SearchState]:
        return dict(self.states)

    async def load_marketplaces(self) -> dict[str, MarketplaceStatus]:
        return dict(self.marketplaces)

    async def load_blocklist(self) -> list[BlockedSeller]:
        return list(self.blocklist)

    async def load_profiles(self) -> dict[str, Profile]:
        return dict(self.profiles)

    async def get_seen(self, marketplace: str, ids: list[str]) -> dict[str, SeenRow]:
        return {i: self.seen[(marketplace, i)] for i in ids if (marketplace, i) in self.seen}

    async def insert_seen(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            key = (r["marketplace"], r["external_id"])
            if key not in self.seen:
                self.seen[key] = SeenRow(**r)

    async def touch_seen(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            key = (r["marketplace"], r["external_id"])
            if key in self.seen:
                self.seen[key] = self.seen[key].model_copy(update=r)

    async def get_listings(self, marketplace: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        return {i: self.listings[(marketplace, i)] for i in ids if (marketplace, i) in self.listings}

    async def upsert_listing(self, row: dict[str, Any]) -> str:
        key = (row["marketplace"], row["external_id"])
        if key in self.listings:
            self.listings[key].update(row)
        else:
            self._next_id += 1
            self.listings[key] = {**row, "id": f"listing-{self._next_id}"}
        return self.listings[key]["id"]

    async def upsert_listings(self, rows: list[dict[str, Any]]) -> dict[str, str]:
        return {r["external_id"]: await self.upsert_listing(r) for r in rows}

    async def update_listing(self, listing_id: str, fields: dict[str, Any]) -> None:
        for row in self.listings.values():
            if row["id"] == listing_id:
                row.update(fields)

    async def insert_price_history(self, rows: list[dict[str, Any]]) -> None:
        self.price_history.extend(rows)

    async def insert_matches(self, rows: list[dict[str, Any]]) -> None:
        existing = {(m["search_id"], m["listing_id"]) for m in self.matches}
        for r in rows:
            if (r["search_id"], r["listing_id"]) not in existing:
                self._next_id += 1
                self.matches.append({"id": f"match-{self._next_id}", **r})
                existing.add((r["search_id"], r["listing_id"]))

    async def save_state(self, search_id: str, marketplace: str, fields: dict[str, Any]) -> None:
        key = (search_id, marketplace)
        merged = {**self.state_fields.get(key, {}), **fields}
        self.state_fields[key] = merged
        prev = self.states.get(key) or SearchState(search_id=search_id, marketplace=marketplace)
        allowed = {k: v for k, v in merged.items() if k in SearchState.model_fields}
        self.states[key] = prev.model_copy(update=allowed)

    async def update_marketplace(self, code: str, fields: dict[str, Any]) -> None:
        self.marketplace_updates.setdefault(code, {}).update(fields)
        if code in self.marketplaces:
            allowed = {k: v for k, v in fields.items() if k in MarketplaceStatus.model_fields}
            self.marketplaces[code] = self.marketplaces[code].model_copy(update=allowed)

    async def update_heartbeat(self, fields: dict[str, Any]) -> None:
        self.heartbeat.update(fields)

    async def pending_matches(self) -> list[PendingMatch]:
        by_id = {row["id"]: row for row in self.listings.values()}
        titles = {s.id: s.title for s in self.searches}
        out = []
        for m in self.matches:
            if m.get("notified_at") is not None:
                continue
            lr = by_id[m["listing_id"]]
            listing = Listing(**{**lr, "description": lr.get("description_snippet")})
            out.append(PendingMatch(
                match_id=m["id"], user_id=m["user_id"], listing_id=m["listing_id"],
                search_title=titles.get(m["search_id"], ""), listing=listing,
            ))
        return out

    async def mark_notified(self, match_ids: list[str], at: datetime) -> None:
        ids = set(match_ids)
        for m in self.matches:
            if m["id"] in ids:
                m["notified_at"] = at

    async def link_telegram(self, code: str, chat_id: int) -> bool:
        return False

    async def prune(self) -> dict[str, Any]:
        return {}


class DryRunStore(MemoryStore):
    """Reads searches/state/blocklist/seen from a real store, writes only to memory."""

    def __init__(self, base: SupabaseStore):
        super().__init__()
        self.base = base

    async def aclose(self) -> None:
        await self.base.aclose()

    async def load_searches(self) -> list[SavedSearch]:
        self.searches = await self.base.load_searches()
        return self.searches

    async def load_states(self) -> dict[tuple[str, str], SearchState]:
        self.states = await self.base.load_states()
        return dict(self.states)

    async def load_marketplaces(self) -> dict[str, MarketplaceStatus]:
        self.marketplaces = await self.base.load_marketplaces()
        return dict(self.marketplaces)

    async def load_blocklist(self) -> list[BlockedSeller]:
        return await self.base.load_blocklist()

    async def get_seen(self, marketplace: str, ids: list[str]) -> dict[str, SeenRow]:
        real = await self.base.get_seen(marketplace, ids)
        return {**real, **await super().get_seen(marketplace, ids)}
