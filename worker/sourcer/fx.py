"""Live exchange rates (European Central Bank reference rates via frankfurter.dev).

Rates are cached in the fx_rates table and refreshed at most every 12 hours, so a
30-minute poll normally makes no FX request at all. If the API is unreachable the
last stored rates are used, and failing that the fixed table in config.FX_TO_SGD.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from .config import FX_TO_SGD, set_live_rates

log = logging.getLogger(__name__)

FX_URL = "https://api.frankfurter.dev/v1/latest"
MAX_AGE = timedelta(hours=12)


async def fetch_rates(client: httpx.AsyncClient) -> dict[str, Decimal]:
    """{currency: SGD per 1 unit} for every currency in the fixed table."""
    symbols = ",".join(c for c in FX_TO_SGD if c != "SGD")
    r = await client.get(FX_URL, params={"base": "SGD", "symbols": symbols}, timeout=15)
    r.raise_for_status()
    per_sgd: dict[str, Any] = r.json()["rates"]
    rates = {"SGD": Decimal(1)}
    for cur, v in per_sgd.items():
        d = Decimal(str(v))
        if d > 0:
            rates[cur.upper()] = Decimal(1) / d
    return rates


async def refresh_rates(store: Any, client: httpx.AsyncClient, now: datetime) -> dict[str, Decimal]:
    """Load cached rates, refetch if stale, and make them the ones convert() uses."""
    stored: dict[str, tuple[Decimal, datetime]] = await store.load_fx_rates()
    rates = {c: r for c, (r, _) in stored.items()}
    stale = not stored or min(t for _, t in stored.values()) < now - MAX_AGE
    if stale:
        try:
            rates = await fetch_rates(client)
            await store.save_fx_rates(rates, now)
            log.info("fx rates refreshed: %d currencies", len(rates))
        except Exception as e:  # never let FX break polling
            log.warning("fx refresh failed, using %s rates: %s", "stored" if rates else "fixed", e)
    set_live_rates(rates)
    return rates
