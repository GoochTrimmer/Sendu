"""End-to-end runs against a fake marketplace + MemoryStore."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from conftest import mk_listing, mk_search

from sourcer.models import BlockedSeller, Listing, Profile, SearchPage
from sourcer.notify import send_pending
from sourcer.ratelimit import MarketplaceBlocked
from sourcer.runner import RunOptions, run
from sourcer.store import MemoryStore


class FakeAdapter:
    code = "grailed"

    def __init__(self, listings: list[Listing], page_size: int = 50, block: bool = False):
        self.listings = listings
        self.page_size = page_size
        self.block = block
        self.calls = 0

    async def search(self, client, query, cursor=None) -> SearchPage:
        self.calls += 1
        if self.block:
            raise MarketplaceBlocked("grailed: HTTP 429")
        start = int(cursor or 0)
        chunk = self.listings[start : start + self.page_size]
        nxt = str(start + self.page_size) if start + self.page_size < len(self.listings) else None
        return SearchPage(listings=chunk, next_cursor=nxt)

    def is_blocked_response(self, resp: httpx.Response) -> bool:
        return False


class FakeTelegram:
    enabled = True

    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.admin_msgs: list[str] = []

    async def send_match(self, chat_id, m):
        self.sent.append((chat_id, m.listing.external_id))

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, "DIGEST:" + text))

    async def admin(self, text):
        self.admin_msgs.append(text)

    async def get_updates(self):
        return []


def now_listing(eid: str, minutes_ago: int = 0, **kw) -> Listing:
    kw.setdefault("listed_at", datetime.now(UTC) - timedelta(minutes=minutes_ago))
    return mk_listing(eid, **kw)


async def do_run(settings, store, adapter, tg=None, **opts):
    return await run(settings, store, {"grailed": adapter}, RunOptions(max_runtime=30, force=True, **opts), tg)


def profile(uid="u1", chat=111):
    return Profile(id=uid, telegram_chat_id=chat, notify_mode="instant")


async def test_first_run_is_silent_then_only_new_listings_alert(fast_settings, monkeypatch):
    monkeypatch.setattr("sourcer.notify.asyncio.sleep", _no_sleep)
    store = MemoryStore(searches=[mk_search()], profiles={"u1": profile()})
    adapter = FakeAdapter([now_listing("a"), now_listing("b")])
    tg = FakeTelegram()

    summary = await do_run(fast_settings, store, adapter, tg)
    assert summary["status"] == "ok"
    assert len(store.matches) == 2
    assert all(m["is_initial"] and m["notified_at"] for m in store.matches)
    assert tg.sent == []  # no flood on the first run

    adapter.listings = [now_listing("c"), *adapter.listings]
    await do_run(fast_settings, store, adapter, tg)
    assert [eid for _, eid in tg.sent] == ["c"]
    new = [m for m in store.matches if not m["is_initial"]]
    assert len(new) == 1 and new[0]["notified_at"] is not None

    # third run, nothing new -> nothing sent
    await do_run(fast_settings, store, adapter, tg)
    assert len(tg.sent) == 1


async def test_spam_hidden_when_toggle_on_and_shown_when_off(fast_settings):
    spam = now_listing("spam", title="YOHJI RICK OWENS CDG MARGIELA jacket")
    on = MemoryStore(searches=[mk_search()])
    await do_run(fast_settings, on, FakeAdapter([spam]))
    assert on.matches == []
    assert on.state_fields[("s1", "grailed")]["last_run_stats"]["spam"] == 1

    off = MemoryStore(searches=[mk_search(spam_filter_enabled=False)])
    await do_run(fast_settings, off, FakeAdapter([spam]))
    assert len(off.matches) == 1


async def test_blocked_seller_personal_vs_shared(fast_settings):
    listing = now_listing("x", seller_id="bot-9")
    s1, s2 = mk_search(id="s1", user_id="u1"), mk_search(id="s2", user_id="u2")
    personal = BlockedSeller(marketplace="grailed", seller_id="bot-9", scope="personal", owner_id="u1")
    store = MemoryStore(searches=[s1, s2], blocklist=[personal])
    await do_run(fast_settings, store, FakeAdapter([listing]))
    assert {m["user_id"] for m in store.matches} == {"u2"}

    shared = BlockedSeller(marketplace="grailed", seller_id="bot-9", scope="shared")
    store2 = MemoryStore(searches=[s1, s2], blocklist=[shared])
    await do_run(fast_settings, store2, FakeAdapter([listing]))
    assert store2.matches == []


async def test_overlapping_searches_both_get_new_listing(fast_settings, monkeypatch):
    monkeypatch.setattr("sourcer.notify.asyncio.sleep", _no_sleep)
    s1 = mk_search(id="s1", user_id="u1", title="yohji")
    s2 = mk_search(id="s2", user_id="u2", title="yohji coat")
    store = MemoryStore(searches=[s1, s2], profiles={"u1": profile("u1", 1), "u2": profile("u2", 2)})
    adapter = FakeAdapter([now_listing("a")])
    tg = FakeTelegram()
    await do_run(fast_settings, store, adapter, tg)  # silent seed for both
    adapter.listings = [now_listing("new"), *adapter.listings]
    await do_run(fast_settings, store, adapter, tg)
    # s1 marks "new" as seen first; s2 must still get it.
    assert sorted(tg.sent) == [(1, "new"), (2, "new")]


async def test_price_drop_and_bump_do_not_alert(fast_settings, monkeypatch):
    monkeypatch.setattr("sourcer.notify.asyncio.sleep", _no_sleep)
    store = MemoryStore(searches=[mk_search()], profiles={"u1": profile()})
    a = now_listing("a", minutes_ago=60, price=Decimal("100"))
    adapter = FakeAdapter([a])
    tg = FakeTelegram()
    await do_run(fast_settings, store, adapter, tg)

    bumped = a.model_copy(update={"price": Decimal("80"), "listed_at": datetime.now(UTC)})
    adapter.listings = [bumped]
    await do_run(fast_settings, store, adapter, tg)
    row = store.listings[("grailed", "a")]
    assert row["price"] == Decimal("80") and row["previous_price"] == Decimal("100")
    assert row["price_dropped_at"] is not None
    assert row["is_bumped"] is True
    assert [h["price"] for h in store.price_history] == [Decimal("100"), Decimal("80")]
    assert tg.sent == []


async def test_paging_stops_after_known_streak(fast_settings):
    old = [now_listing(f"o{i}", minutes_ago=5) for i in range(20)]
    store = MemoryStore(searches=[mk_search()])
    adapter = FakeAdapter(old, page_size=5)
    await do_run(fast_settings, store, adapter)
    assert adapter.calls == 3  # first run: max_pages
    adapter.calls = 0
    adapter.listings = [now_listing("n1"), *old]
    await do_run(fast_settings, store, adapter)
    # page 1 = n1 + 4 known, page 2 = first known in the 5-streak -> stop
    assert adapter.calls == 2


async def test_block_pauses_marketplace_and_alerts_admin_once(fast_settings):
    store = MemoryStore(searches=[mk_search(id="a"), mk_search(id="b")])
    adapter = FakeAdapter([], block=True)
    tg = FakeTelegram()
    summary = await do_run(fast_settings, store, adapter, tg)
    assert adapter.calls == 1  # no retry, no further pairs
    upd = store.marketplace_updates["grailed"]
    assert upd["consecutive_failures"] == 1
    assert timedelta(minutes=59) < upd["paused_until"] - datetime.now(UTC) <= timedelta(hours=1)
    assert len(tg.admin_msgs) == 1
    assert summary["marketplaces"]["grailed"]["status"] == "blocked"


async def test_dry_run_writes_nothing_persistent(fast_settings, capsys):
    store = MemoryStore(searches=[mk_search()])
    await do_run(fast_settings, store, FakeAdapter([now_listing("a")]), dry_run=True)
    assert "[initial]" in capsys.readouterr().out
    assert store.heartbeat == {}  # heartbeat untouched in dry-run


async def test_digest_when_many_matches(fast_settings):
    store = MemoryStore(searches=[mk_search()], profiles={"u1": profile()})
    adapter = FakeAdapter([now_listing("seed")])
    await do_run(fast_settings, store, adapter)
    adapter.listings = [now_listing(f"n{i}") for i in range(8)] + adapter.listings
    tg = FakeTelegram()
    await do_run(fast_settings, store, adapter, tg)
    assert len(tg.sent) == 1 and tg.sent[0][1].startswith("DIGEST:")
    assert "8 new matches" in tg.sent[0][1]


async def test_notify_off_marks_without_sending(fast_settings):
    store = MemoryStore(searches=[mk_search()], profiles={"u1": Profile(id="u1", telegram_chat_id=1, notify_mode="off")})
    await store.insert_matches([{"search_id": "s1", "listing_id": await store.upsert_listing(
        {"marketplace": "grailed", "external_id": "z", "title": "t", "url": "u", "image_urls": []}
    ), "user_id": "u1", "notified_at": None}])
    tg = FakeTelegram()
    await send_pending(tg, store, await store.load_profiles(), fast_settings)
    assert tg.sent == [] and store.matches[0]["notified_at"] is not None


async def _no_sleep(_):
    return None


async def test_resurfaced_old_listing_does_not_stop_paging(fast_settings):
    """Mercari sorts by update time: an item created a year ago can sit at the top."""
    year_ago = datetime.now(UTC) - timedelta(days=365)
    store = MemoryStore(searches=[mk_search()])
    adapter = FakeAdapter([now_listing("seed")], page_size=2)
    await do_run(fast_settings, store, adapter)
    resurfaced = now_listing("old", listed_at=year_ago, sort_time=datetime.now(UTC))
    fresh = [now_listing(f"n{i}") for i in range(3)]
    adapter.listings = [resurfaced, *fresh, now_listing("seed")]
    adapter.calls = 0
    await do_run(fast_settings, store, adapter)
    # sort_time is recent, so paging continues past page 1 and finds n1, n2
    matched = {k[1] for k in store.listings}
    assert {"old", "n0", "n1", "n2"} <= matched
    assert adapter.calls >= 2
