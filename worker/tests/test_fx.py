from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from conftest import mk_listing

from sourcer import config
from sourcer.fx import refresh_rates
from sourcer.notify import format_price
from sourcer.store import MemoryStore

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
API = {"amount": 1.0, "base": "SGD", "date": "2026-11-03",
       "rates": {"PHP": 49.534, "MYR": 3.1823, "IDR": 14157, "JPY": 123.55, "HKD": 6.1241}}


@pytest.fixture(autouse=True)
def _reset_rates():
    yield
    config.set_live_rates({})


def client_returning(payload=None, status=200, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        return httpx.Response(status, json=payload or {})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_refresh_fetches_stores_and_applies_live_rates():
    store, calls = MemoryStore(), []
    async with client_returning(API, calls=calls) as c:
        rates = await refresh_rates(store, c, NOW)
    assert len(calls) == 1 and "base=SGD" in calls[0]
    assert rates["PHP"] == Decimal(1) / Decimal("49.534")
    assert set(store.fx_rates) >= {"SGD", "PHP", "MYR", "IDR"}
    assert config.convert(Decimal("49534"), "PHP", "SGD").quantize(Decimal("0.01")) == Decimal("1000.00")


async def test_fresh_cache_skips_the_api():
    store, calls = MemoryStore(), []
    store.fx_rates = {"PHP": (Decimal("0.02"), NOW - timedelta(hours=2))}
    async with client_returning(API, calls=calls) as c:
        await refresh_rates(store, c, NOW)
    assert calls == []
    assert config.convert(Decimal("100"), "PHP", "SGD") == Decimal("2.00")


async def test_api_failure_falls_back_to_stored_then_fixed():
    store = MemoryStore()
    store.fx_rates = {"PHP": (Decimal("0.03"), NOW - timedelta(days=3))}
    async with client_returning(status=500) as c:
        await refresh_rates(store, c, NOW)
    assert config.convert(Decimal("100"), "PHP", "SGD") == Decimal("3.00")  # stale stored rate
    assert config.convert(Decimal("1"), "MYR", "SGD") == config.FX_TO_SGD["MYR"]  # fixed fallback


def test_format_price_shows_local_and_sgd():
    config.set_live_rates({"SGD": Decimal(1), "PHP": Decimal(1) / Decimal("49.534")})
    x = mk_listing("1", price=Decimal("1280"), currency="PHP")
    assert format_price(x) == "1,280 PHP (≈ S$25.84)"
    assert format_price(mk_listing("1", price=Decimal("110"), currency="SGD")) == "110 SGD"
    assert format_price(mk_listing("1", price=Decimal("39.90"), currency="MYR")).startswith("39.90 MYR (≈ S$")
