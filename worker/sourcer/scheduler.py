"""Decide which (saved_search, marketplace) pairs to run, and when to stop paging.

README 4.2 (due-ness queue) and 4.4 (paging stop rules).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import Listing, MarketplaceStatus, SavedSearch, SearchState, SeenRow


@dataclass
class Pair:
    search: SavedSearch
    marketplace: str
    state: SearchState
    overdue: float

    @property
    def is_first_run(self) -> bool:
        return not self.state.initialized


def overdue_ratio(
    last_polled_at: datetime | None,
    priority: str,
    now: datetime,
    intervals: dict[str, timedelta],
) -> float:
    if last_polled_at is None:
        return math.inf
    target = intervals.get(priority, intervals["normal"]).total_seconds()
    return (now - last_polled_at).total_seconds() / target


def marketplace_available(mp: MarketplaceStatus | None, now: datetime) -> bool:
    if mp is None or not mp.enabled:
        return False
    return mp.paused_until is None or mp.paused_until <= now


def build_queues(
    searches: list[SavedSearch],
    states: dict[tuple[str, str], SearchState],
    marketplaces: dict[str, MarketplaceStatus],
    now: datetime,
    intervals: dict[str, timedelta],
    *,
    only_marketplace: str | None = None,
    only_search: str | None = None,
    force: bool = False,
) -> dict[str, list[Pair]]:
    """One queue per available marketplace, most overdue first.

    Pairs with overdue_ratio < 1 are skipped (unless `force`, used by --only-search
    and dry runs). Never-polled pairs have an infinite ratio so they run first.
    """
    queues: dict[str, list[Pair]] = {}
    for s in searches:
        if s.is_paused or (only_search and s.id != only_search):
            continue
        for mp in s.marketplaces:
            if only_marketplace and mp != only_marketplace:
                continue
            if not marketplace_available(marketplaces.get(mp), now):
                continue
            state = states.get((s.id, mp)) or SearchState(search_id=s.id, marketplace=mp)
            ratio = overdue_ratio(state.last_polled_at, s.priority, now, intervals)
            if ratio < 1 and not force:
                continue
            queues.setdefault(mp, []).append(Pair(s, mp, state, ratio))
    for q in queues.values():
        # Most overdue first; ties broken by priority so urgent wins.
        rank = {"urgent": 0, "normal": 1, "daily": 2}
        q.sort(key=lambda p: (-p.overdue, rank.get(p.search.priority, 1)))
    return queues


def is_known(seen: SeenRow | None, last_polled_at: datetime | None) -> bool:
    """Has *this search* already had a chance to see the listing?

    seen_listings is global, so "seen by some other search" isn't enough: a
    listing is known to a search only if it was first seen before that search's
    previous poll. Otherwise two overlapping searches would steal each other's
    new listings.
    """
    if seen is None or last_polled_at is None:
        return False
    return seen.first_seen_at <= last_polled_at


class PagingTracker:
    """Paging stop rules for one (search, marketplace) pair (README 4.4).

    Stop when any of:
      * `consecutive_limit` consecutive listings were already known,
      * a listing is older than last_polled_at - grace,
      * `max_pages` pages were read, or the marketplace has no next page.
    (The request budget is enforced by the RateLimiter.)
    """

    def __init__(
        self,
        last_polled_at: datetime | None,
        *,
        consecutive_limit: int = 5,
        grace: timedelta = timedelta(hours=2),
        max_pages: int = 3,
    ):
        self.cutoff = last_polled_at - grace if last_polled_at else None
        self.consecutive_limit = consecutive_limit
        self.max_pages = max_pages
        self.pages_read = 0
        self.consecutive_known = 0
        self.stop_reason: str | None = None

    def observe(self, listing: Listing, known: bool) -> bool:
        """Record one listing (in result order). Returns True when paging should stop."""
        if self.stop_reason:
            return True
        self.consecutive_known = self.consecutive_known + 1 if known else 0
        if self.consecutive_known >= self.consecutive_limit:
            self.stop_reason = "known"
        elif self.cutoff and listing.effective_sort_time and listing.effective_sort_time < self.cutoff:
            self.stop_reason = "old"
        return self.stop_reason is not None

    def page_done(self, has_next: bool) -> bool:
        """Call after each page. Returns True when paging should stop."""
        self.pages_read += 1
        if self.stop_reason:
            return True
        if not has_next:
            self.stop_reason = "end"
        elif self.pages_read >= self.max_pages:
            self.stop_reason = "max_pages"
        return self.stop_reason is not None
