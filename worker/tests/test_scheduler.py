import math
from datetime import UTC, datetime, timedelta

from conftest import mk_listing, mk_search

from sourcer.config import TARGET_INTERVALS
from sourcer.models import MarketplaceStatus, SearchState, SeenRow
from sourcer.scheduler import PagingTracker, build_queues, is_known, overdue_ratio

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
MPS = {c: MarketplaceStatus(code=c, name=c) for c in ("ebay", "grailed", "mercari_jp")}


def test_overdue_ratio():
    assert overdue_ratio(None, "normal", NOW, TARGET_INTERVALS) == math.inf
    assert overdue_ratio(NOW - timedelta(hours=1), "normal", NOW, TARGET_INTERVALS) == 0.5
    assert overdue_ratio(NOW - timedelta(hours=1), "urgent", NOW, TARGET_INTERVALS) == 2.0


def test_queue_orders_by_overdue_and_skips_not_due():
    a = mk_search(id="a", priority="urgent", marketplaces=["grailed", "ebay"])
    b = mk_search(id="b", priority="normal", marketplaces=["grailed"])
    c = mk_search(id="c", priority="daily", marketplaces=["grailed"])
    states = {
        ("a", "grailed"): SearchState(search_id="a", marketplace="grailed", initialized=True,
                                      last_polled_at=NOW - timedelta(minutes=45)),  # 1.5
        ("b", "grailed"): SearchState(search_id="b", marketplace="grailed", initialized=True,
                                      last_polled_at=NOW - timedelta(hours=6)),  # 3.0
        ("c", "grailed"): SearchState(search_id="c", marketplace="grailed", initialized=True,
                                      last_polled_at=NOW - timedelta(hours=2)),  # < 1 -> skip
    }
    q = build_queues([a, b, c], states, MPS, NOW, TARGET_INTERVALS)
    assert [p.search.id for p in q["grailed"]] == ["b", "a"]
    assert [p.search.id for p in q["ebay"]] == ["a"]  # never polled
    assert q["ebay"][0].is_first_run


def test_urgent_search_runs_on_every_half_hour_tick():
    # Previous tick stamped last_polled_at 51 s after :30; the :00 tick must still run it.
    s = mk_search(id="a", priority="urgent", marketplaces=["grailed"])
    st = {("a", "grailed"): SearchState(search_id="a", marketplace="grailed", initialized=True,
                                        last_polled_at=NOW - timedelta(minutes=29, seconds=9))}
    assert [p.search.id for p in build_queues([s], st, MPS, NOW, TARGET_INTERVALS)["grailed"]] == ["a"]
    # ...but a run only 15 minutes later (e.g. a manual trigger) does not re-poll.
    st[("a", "grailed")].last_polled_at = NOW - timedelta(minutes=15)
    assert build_queues([s], st, MPS, NOW, TARGET_INTERVALS) == {}


def test_queue_skips_paused_marketplace_and_paused_search():
    mps = {**MPS, "ebay": MarketplaceStatus(code="ebay", name="eBay", paused_until=NOW + timedelta(hours=1))}
    s = mk_search(id="a", marketplaces=["grailed", "ebay"])
    paused = mk_search(id="p", is_paused=True)
    q = build_queues([s, paused], {}, mps, NOW, TARGET_INTERVALS)
    assert set(q) == {"grailed"}
    assert [p.search.id for p in q["grailed"]] == ["a"]


def test_queue_filters_and_force():
    s = mk_search(id="a", marketplaces=["grailed", "ebay"])
    st = {("a", "grailed"): SearchState(search_id="a", marketplace="grailed", last_polled_at=NOW)}
    assert build_queues([s], st, MPS, NOW, TARGET_INTERVALS, only_marketplace="grailed") == {}
    q = build_queues([s], st, MPS, NOW, TARGET_INTERVALS, only_marketplace="grailed", force=True)
    assert len(q["grailed"]) == 1


def test_is_known_is_per_search():
    seen = SeenRow(marketplace="grailed", external_id="1", first_seen_at=NOW - timedelta(minutes=10))
    assert not is_known(None, NOW)
    assert not is_known(seen, None)  # first run
    assert is_known(seen, NOW)  # seen before this search's last poll
    # first seen by ANOTHER search after this one last polled -> still new for this one
    assert not is_known(seen, NOW - timedelta(hours=1))


def test_paging_stops_after_five_consecutive_known():
    t = PagingTracker(NOW - timedelta(hours=1))
    pattern = [True, True, False, True, True, True, True]
    results = [t.observe(mk_listing(str(i), listed_at=NOW), k) for i, k in enumerate(pattern)]
    assert results[-1] is False and t.stop_reason is None  # only 4 in a row
    assert t.observe(mk_listing("x", listed_at=NOW), True) is True
    assert t.stop_reason == "known"


def test_paging_stops_on_old_listing():
    t = PagingTracker(NOW - timedelta(hours=1), grace=timedelta(hours=2))
    assert not t.observe(mk_listing("1", listed_at=NOW - timedelta(hours=2, minutes=59)), False)
    assert t.observe(mk_listing("2", listed_at=NOW - timedelta(hours=3, minutes=1)), False)
    assert t.stop_reason == "old"


def test_paging_no_old_rule_on_first_run():
    t = PagingTracker(None)
    assert not t.observe(mk_listing("1", listed_at=NOW - timedelta(days=300)), False)


def test_paging_max_pages_and_end():
    t = PagingTracker(None, max_pages=3)
    assert not t.page_done(True)
    assert not t.page_done(True)
    assert t.page_done(True) and t.stop_reason == "max_pages"
    t2 = PagingTracker(None)
    assert t2.page_done(False) and t2.stop_reason == "end"
