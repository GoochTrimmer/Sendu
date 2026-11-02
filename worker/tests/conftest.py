from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from sourcer.config import Settings
from sourcer.models import Listing, SavedSearch

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def mk_listing(eid: str, title: str = "Yohji Yamamoto pour homme coat", **kw: Any) -> Listing:
    base: dict[str, Any] = {
        "marketplace": "grailed",
        "external_id": eid,
        "title": title,
        "price": Decimal("100"),
        "currency": "USD",
        "seller_id": "seller-1",
        "seller_name": "someone",
        "url": f"https://example.com/{eid}",
        "image_urls": [f"https://img.example.com/{eid}.jpg"],
        "listed_at": datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
    }
    base.update(kw)
    return Listing(**base)


def mk_search(**kw: Any) -> SavedSearch:
    base: dict[str, Any] = {
        "id": "s1",
        "user_id": "u1",
        "title": "yohji pour homme",
        "query": "yohji pour homme",
        "marketplaces": ["grailed"],
    }
    base.update(kw)
    return SavedSearch(**base)


@pytest.fixture
def fast_settings() -> Settings:
    return Settings(request_interval=0, jitter_max=0, retry_delay=0)
