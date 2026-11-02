"""Pydantic models shared by adapters, matching, scheduler and store."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Priority = Literal["urgent", "normal", "daily"]


class Listing(BaseModel):
    marketplace: str
    external_id: str
    title: str
    description: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    size: str | None = None
    brand: str | None = None
    seller_id: str | None = None
    seller_name: str | None = None
    url: str
    image_urls: list[str] = Field(default_factory=list)  # first 4 only
    listed_at: datetime | None = None  # when it was really listed (shown in the feed)
    # The timestamp the marketplace's "newest" sort actually orders by. Mercari sorts by
    # last update (price cuts re-surface old items), Yahoo/Grailed by listing time.
    # Drives the paging stop rule and bump detection. Defaults to listed_at.
    sort_time: datetime | None = None
    raw_status: str | None = None  # 'active' | 'sold' | 'removed' if the API says

    @field_validator("image_urls")
    @classmethod
    def _first_four(cls, v: list[str]) -> list[str]:
        return [u for u in v if u][:4]

    @property
    def effective_sort_time(self) -> datetime | None:
        return self.sort_time or self.listed_at

    @property
    def key(self) -> tuple[str, str]:
        return (self.marketplace, self.external_id)


class SearchPage(BaseModel):
    listings: list[Listing]
    next_cursor: str | None = None


class SearchOverride(BaseModel):
    marketplace: str
    query_override: str | None = None
    exclude_enabled: bool | None = None  # None = inherit
    extra_exclude_terms: list[str] = Field(default_factory=list)


class SavedSearch(BaseModel):
    id: str
    user_id: str
    title: str
    query: str
    group_name: str | None = None
    marketplaces: list[str]
    include_terms: list[str] = Field(default_factory=list)
    exclude_terms: list[str] = Field(default_factory=list)
    exclude_enabled: bool = True
    spam_filter_enabled: bool = True
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    currency: str = "SGD"
    sizes: list[str] = Field(default_factory=list)
    priority: Priority = "normal"
    is_paused: bool = False
    overrides: dict[str, SearchOverride] = Field(default_factory=dict)

    def query_for(self, marketplace: str) -> str:
        o = self.overrides.get(marketplace)
        return (o.query_override or "").strip() or self.query if o else self.query

    def exclude_enabled_for(self, marketplace: str) -> bool:
        o = self.overrides.get(marketplace)
        if o and o.exclude_enabled is not None:
            return o.exclude_enabled
        return self.exclude_enabled

    def exclude_terms_for(self, marketplace: str) -> list[str]:
        o = self.overrides.get(marketplace)
        return [*self.exclude_terms, *(o.extra_exclude_terms if o else [])]


class SearchState(BaseModel):
    search_id: str
    marketplace: str
    initialized: bool = False
    last_polled_at: datetime | None = None
    consecutive_errors: int = 0


class SeenRow(BaseModel):
    marketplace: str
    external_id: str
    first_seen_at: datetime
    last_seen_at: datetime | None = None
    last_price: Decimal | None = None
    last_listed_at: datetime | None = None  # last seen sort_time (column name kept for compat)


class BlockedSeller(BaseModel):
    marketplace: str
    seller_id: str
    seller_name: str | None = None
    scope: Literal["personal", "shared"]
    owner_id: str | None = None


class MarketplaceStatus(BaseModel):
    code: str
    name: str
    enabled: bool = True
    paused_until: datetime | None = None
    consecutive_failures: int = 0


class Profile(BaseModel):
    id: str
    display_name: str | None = None
    telegram_chat_id: int | None = None
    notify_mode: Literal["instant", "digest", "off"] = "instant"


class PendingMatch(BaseModel):
    """A match row joined with its listing and search, waiting for a Telegram alert."""

    match_id: str
    user_id: str
    search_title: str
    listing: Listing
    listing_id: str
