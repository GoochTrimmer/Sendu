"""Environment variables and tunable constants."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

WORKER_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = WORKER_DIR / "data"

load_dotenv(WORKER_DIR / ".env")

# How often each priority should be polled.
TARGET_INTERVALS: dict[str, timedelta] = {
    "urgent": timedelta(minutes=30),
    "normal": timedelta(hours=2),
    "daily": timedelta(hours=24),
}

# Fixed FX table for Phase 1: how many SGD one unit of each currency is worth.
# Rough values; only used for min/max price filters.
FX_TO_SGD: dict[str, Decimal] = {
    "SGD": Decimal("1"),
    "USD": Decimal("1.30"),
    "EUR": Decimal("1.45"),
    "GBP": Decimal("1.70"),
    "JPY": Decimal("0.0088"),
    "AUD": Decimal("0.86"),
    "CAD": Decimal("0.95"),
    "HKD": Decimal("0.167"),
    "CNY": Decimal("0.18"),
    "KRW": Decimal("0.00095"),
}

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def convert(amount: Decimal | None, from_cur: str | None, to_cur: str) -> Decimal | None:
    """Convert using the fixed table. Returns None if either currency is unknown."""
    if amount is None or not from_cur:
        return None
    src, dst = FX_TO_SGD.get(from_cur.upper()), FX_TO_SGD.get(to_cur.upper())
    if src is None or dst is None:
        return None
    return amount * src / dst


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass
class Settings:
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_admin_chat_id: str | None = None
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    ebay_marketplace_id: str = "EBAY_US"
    grailed_algolia_app_id: str = "MNRWEFSS2Q"
    grailed_algolia_api_key: str | None = None
    grailed_algolia_index: str = "Listing_by_date_added_production"

    request_interval: float = 6.0       # min seconds between requests to one marketplace
    jitter_max: float = 3.0             # plus 0..jitter_max random seconds
    spam_brand_threshold: int = 3
    max_pages: int = 3
    consecutive_seen_stop: int = 5
    listed_at_grace: timedelta = timedelta(hours=2)
    digest_threshold: int = 5           # > this many matches per run => one digest message
    digest_top_n: int = 10
    retry_delay: float = 10.0           # wait before retrying a 5xx / network error once
    backoff_base: timedelta = timedelta(hours=1)
    backoff_max: timedelta = timedelta(hours=24)
    heartbeat_stale_after: timedelta = timedelta(hours=3)
    target_intervals: dict[str, timedelta] = field(default_factory=lambda: dict(TARGET_INTERVALS))

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            supabase_url=_env("SUPABASE_URL"),
            supabase_service_role_key=_env("SUPABASE_SERVICE_ROLE_KEY"),
            telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
            telegram_admin_chat_id=_env("TELEGRAM_ADMIN_CHAT_ID"),
            ebay_client_id=_env("EBAY_CLIENT_ID"),
            ebay_client_secret=_env("EBAY_CLIENT_SECRET"),
            ebay_marketplace_id=_env("EBAY_MARKETPLACE_ID", "EBAY_US") or "EBAY_US",
            grailed_algolia_app_id=_env("GRAILED_ALGOLIA_APP_ID", "MNRWEFSS2Q") or "MNRWEFSS2Q",
            grailed_algolia_api_key=_env("GRAILED_ALGOLIA_API_KEY"),
            grailed_algolia_index=_env("GRAILED_ALGOLIA_INDEX", "Listing_by_date_added_production")
            or "Listing_by_date_added_production",
            request_interval=max(6.0, float(_env("REQUEST_INTERVAL_SECONDS", "6") or 6)),
            spam_brand_threshold=int(_env("SPAM_BRAND_THRESHOLD", "3") or 3),
        )

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token)
