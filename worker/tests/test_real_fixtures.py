"""Invariants on REAL captured responses (saved by `python -m sourcer probe <mp> <q> --save`).

The *_search.json fixtures are hand-made for exact-value unit tests; the *_real.json ones are
real marketplace responses, so these tests only check properties that must always hold.
Skipped for marketplaces without a capture.
"""

import pytest
from conftest import FIXTURES, load_fixture

from sourcer.adapters.carousell import COUNTRIES, CarousellAdapter
from sourcer.adapters.ebay import EbayAdapter
from sourcer.adapters.grailed import GrailedAdapter
from sourcer.adapters.mercari_jp import MercariJPAdapter
from sourcer.adapters.yahoo_auctions import YahooAuctionsAdapter

PARSERS = {
    "ebay": EbayAdapter.parse,
    "grailed": GrailedAdapter.parse,
    "mercari_jp": MercariJPAdapter.parse,
    "yahoo_auctions": YahooAuctionsAdapter.parse,
    **{code: CarousellAdapter(code).parse for code in COUNTRIES},
}


@pytest.mark.parametrize("mp", sorted(PARSERS))
def test_real_capture_parses_cleanly(mp):
    if not (FIXTURES / f"{mp}_real.json").exists():
        pytest.skip(f"no real capture for {mp} yet")
    page = PARSERS[mp](load_fixture(f"{mp}_real.json"))
    assert len(page.listings) >= 5, "a real newest-first search should return results"
    ids = [x.external_id for x in page.listings]
    assert len(ids) == len(set(ids)), "duplicate ids in one page"
    for x in page.listings:
        assert x.marketplace == mp and x.title and x.url.startswith("https://")
        assert x.price is not None and x.price >= 0
        assert x.effective_sort_time is not None, f"{x.external_id}: no sort time"
        assert x.seller_id not in ("0", ""), f"{x.external_id}: unusable seller id {x.seller_id!r}"
    with_seller = sum(1 for x in page.listings if x.seller_id)
    assert with_seller >= len(page.listings) * 0.9, "seller ids missing on most listings"
    times = [x.effective_sort_time for x in page.listings]
    # Newest first, allowing a little noise in the marketplace's ordering.
    assert times[0] >= sorted(times)[len(times) // 2]
