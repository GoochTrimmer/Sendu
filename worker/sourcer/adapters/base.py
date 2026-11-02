"""Marketplace adapter protocol and shared helpers.

Adapters only fetch and parse. Rate limiting, paging stop rules, dedup and
matching all live outside the adapters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

from ..config import BROWSER_USER_AGENT
from ..models import SearchPage
from ..ratelimit import MarketplaceBlocked, TransientError

CHALLENGE_MARKERS = (
    "captcha",
    "cf-challenge",
    "challenge-platform",
    "are you a robot",
    "px-captcha",
    "access denied",
)


class MarketplaceAdapter(Protocol):
    code: str

    async def search(
        self, client: httpx.AsyncClient, query: str, cursor: str | None = None
    ) -> SearchPage: ...

    def is_blocked_response(self, resp: httpx.Response) -> bool: ...


class AdapterNotConfigured(Exception):
    """Required credentials / settings are missing for this adapter."""


def browser_headers(accept_language: str = "en-US,en;q=0.9") -> dict[str, str]:
    return {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept-Language": accept_language,
        "Accept": "application/json, text/plain, */*",
    }


def looks_like_challenge(resp: httpx.Response) -> bool:
    """HTML where JSON was expected, containing a captcha / challenge marker."""
    ctype = resp.headers.get("content-type", "")
    if "json" in ctype:
        return False
    body = resp.text[:5000].lower()
    return any(m in body for m in CHALLENGE_MARKERS)


def check_response(adapter: MarketplaceAdapter, resp: httpx.Response) -> None:
    """Translate HTTP outcomes into the worker's error policy (README 4.3)."""
    if resp.status_code in (403, 429) or adapter.is_blocked_response(resp):
        raise MarketplaceBlocked(f"{adapter.code}: HTTP {resp.status_code}")
    if resp.status_code >= 500:
        raise TransientError(f"{adapter.code}: HTTP {resp.status_code}")
    resp.raise_for_status()


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_time(value: Any) -> datetime | None:
    """ISO-8601 string or unix seconds (int / numeric string) -> aware UTC datetime."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            ts = float(value)
            if ts > 1e12:  # milliseconds
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=UTC)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, OSError, OverflowError):
        return None
