"""One poll run: schedule pairs, fetch per marketplace concurrently, match, store, notify."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from .adapters import AdapterNotConfigured, MarketplaceAdapter
from .config import Settings
from .matching import Blocklist, Matcher, load_brands, new_stats
from .models import Listing, MarketplaceStatus, SeenRow
from .notify import Telegram, link_telegram_accounts, send_pending
from .ratelimit import (
    BudgetExhausted,
    MarketplaceBlocked,
    RateLimiter,
    call_with_retry,
    default_budget,
    next_backoff,
)
from .scheduler import PagingTracker, Pair, build_queues, is_known
from .store import listing_row

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class RunOptions:
    max_runtime: float = 150.0
    dry_run: bool = False
    only_marketplace: str | None = None
    only_search: str | None = None
    force: bool = False  # ignore due-ness (dry runs / --only-search)


@dataclass
class RunContext:
    settings: Settings
    store: Any
    adapters: dict[str, MarketplaceAdapter]
    matcher: Matcher
    telegram: Telegram | None
    options: RunOptions
    marketplaces: dict[str, MarketplaceStatus]
    deadline: float
    printed: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# One (search, marketplace) pair
# ---------------------------------------------------------------------------


async def _update_known_listing(
    ctx: RunContext, row: dict[str, Any], fresh: Listing, seen: SeenRow | None, now: datetime
) -> None:
    """Price change / bump / status bookkeeping for a listing we store in full."""
    fields: dict[str, Any] = {"last_seen_at": now}
    old_price = row.get("price")
    if fresh.price is not None and old_price is not None and float(fresh.price) != float(old_price):
        fields.update(price=fresh.price, previous_price=old_price)
        if float(fresh.price) < float(old_price):
            fields["price_dropped_at"] = now
        await ctx.store.insert_price_history(
            [{"listing_id": row["id"], "price": fresh.price, "currency": fresh.currency, "observed_at": now}]
        )
    last_sort = seen.last_listed_at if seen else None
    if fresh.effective_sort_time and last_sort and fresh.effective_sort_time > last_sort:
        fields["is_bumped"] = True  # re-surfaced (bump / relist / edit): flag, never alert
    if fresh.raw_status in ("sold", "removed") and row.get("status") != fresh.raw_status:
        fields["status"] = fresh.raw_status
    await ctx.store.update_listing(row["id"], fields)


async def run_pair(
    ctx: RunContext,
    pair: Pair,
    adapter: MarketplaceAdapter,
    limiter: RateLimiter,
    client: httpx.AsyncClient,
) -> Counter[str]:
    s, mp, state = pair.search, pair.marketplace, pair.state
    query = s.query_for(mp)
    st = ctx.settings
    tracker = PagingTracker(
        state.last_polled_at,
        consecutive_limit=st.consecutive_seen_stop,
        grace=st.listed_at_grace,
        max_pages=st.max_pages,
    )
    stats = new_stats()
    cursor: str | None = None
    handled: set[str] = set()
    candidates: list[Listing] = []
    already_stored: set[str] = set()  # external IDs that already have a full `listings` row
    pages = 0

    while True:
        try:
            page = await call_with_retry(
                limiter,
                lambda c=cursor: adapter.search(client, query, c),
                retry_delay=st.retry_delay,
                deadline=ctx.deadline,
            )
        except BudgetExhausted:
            if pages == 0:
                raise
            break  # keep what we have; the pair will be due again next run
        pages += 1
        now = utcnow()
        listings = [x for x in page.listings if x.external_id not in handled]
        handled.update(x.external_id for x in listings)
        stats["fetched"] += len(listings)
        ids = [x.external_id for x in listings]
        seen = await ctx.store.get_seen(mp, ids)
        stored = await ctx.store.get_listings(mp, ids)
        already_stored.update(stored)

        new_rows, touch_rows = [], []
        for x in listings:
            sr = seen.get(x.external_id)
            known = is_known(sr, state.last_polled_at)
            if not known:
                candidates.append(x)
            if x.external_id in stored:
                await _update_known_listing(ctx, stored[x.external_id], x, sr, now)
            base = {
                "marketplace": mp,
                "external_id": x.external_id,
                "last_seen_at": now,
                "last_price": x.price,
                "last_listed_at": max(
                    filter(None, [x.effective_sort_time, sr.last_listed_at if sr else None]), default=None
                ),
            }
            if sr is None:
                new_rows.append({**base, "first_seen_at": now})
            else:
                touch_rows.append(base)
            tracker.observe(x, known)
        await ctx.store.insert_seen(new_rows)
        await ctx.store.touch_seen(touch_rows)

        if tracker.page_done(page.next_cursor is not None):
            break
        cursor = page.next_cursor

    # Matching pipeline for listings that are new to this search.
    initial = not state.initialized
    stats["new"] = len(candidates)
    now = utcnow()
    passed: list[Listing] = []
    for x in candidates:
        reason = ctx.matcher.evaluate(x, s)
        if reason:
            stats[reason] += 1
            continue
        stats["matched"] += 1
        passed.append(x)
        if ctx.options.dry_run:
            tag = "initial" if initial else "NEW"
            line = (
                f"  [{tag}] {mp:<10} {x.title[:70]!r} {x.price} {x.currency or ''}"
                f" seller={x.seller_id} {x.url}"
            )
            ctx.printed.append(line)
            print(line)

    # Store in bulk: one request per chunk instead of 2 per listing (a first run can match
    # hundreds of listings, and one-by-one writes took minutes).
    ids = await ctx.store.upsert_listings([listing_row(x, now) for x in passed])
    await ctx.store.insert_price_history([
        # Baseline price-history row the first time a listing is stored in full.
        {"listing_id": ids[x.external_id], "price": x.price, "currency": x.currency, "observed_at": now}
        for x in passed
        if x.price is not None and x.external_id not in already_stored
    ])
    await ctx.store.insert_matches([
        {
            "search_id": s.id,
            "listing_id": ids[x.external_id],
            "user_id": s.user_id,
            "is_initial": initial,
            "notified_at": now if initial else None,  # silent first run: no Telegram
            "matched_at": now,
        }
        for x in passed
    ])

    await ctx.store.save_state(s.id, mp, {
        "initialized": True,
        "last_polled_at": utcnow(),  # after the poll: everything first seen above counts as known
        "last_new_count": stats["matched"],
        "last_run_stats": dict(stats) | {"pages": pages, "stop": tracker.stop_reason},
        "consecutive_errors": 0,
        "last_error": None,
    })
    log.info("%s / %s %r: %s pages, %s", s.title, mp, query, pages, dict(stats))
    return stats


# ---------------------------------------------------------------------------
# One marketplace (sequential pairs, own rate limiter)
# ---------------------------------------------------------------------------


async def handle_block(ctx: RunContext, mp: str, message: str) -> None:
    status = ctx.marketplaces.get(mp) or MarketplaceStatus(code=mp, name=mp)
    backoff = next_backoff(status.consecutive_failures, ctx.settings.backoff_base, ctx.settings.backoff_max)
    now = utcnow()
    until = now + backoff
    log.warning("%s blocked (%s); paused until %s", mp, message, until.isoformat())
    if ctx.options.dry_run:
        print(f"  !! {mp} looks blocked ({message}); a real run would pause it until {until:%H:%M} UTC")
        return
    await ctx.store.update_marketplace(mp, {
        "paused_until": until,
        "consecutive_failures": status.consecutive_failures + 1,
        "last_error": message,
        "last_error_at": now,
    })
    if ctx.telegram:
        await ctx.telegram.admin(
            f"⚠️ {status.name} paused until {until:%Y-%m-%d %H:%M} UTC "
            f"({backoff.total_seconds() / 3600:.0f}h backoff): {message}"
        )


async def run_marketplace(
    ctx: RunContext, mp: str, pairs: list[Pair], client: httpx.AsyncClient, budget: int
) -> dict[str, Any]:
    adapter = ctx.adapters[mp]
    limiter = RateLimiter(ctx.settings.request_interval, ctx.settings.jitter_max, budget)
    summary: dict[str, Any] = {"queued": len(pairs), "done": 0, "new": 0, "matched": 0, "errors": 0, "status": "ok"}
    for pair in pairs:
        try:
            stats = await run_pair(ctx, pair, adapter, limiter, client)
            summary["done"] += 1
            summary["new"] += stats["new"]
            summary["matched"] += stats["matched"]
        except BudgetExhausted as e:
            summary["status"] = f"stopped: {e}"
            break
        except MarketplaceBlocked as e:
            summary["status"] = "blocked"
            await handle_block(ctx, mp, str(e))
            break
        except AdapterNotConfigured as e:
            summary["status"] = f"not configured: {e}"
            log.warning("%s skipped: %s", mp, e)
            break
        except Exception as e:  # one bad pair must not stop the marketplace
            summary["errors"] += 1
            msg = f"{type(e).__name__}: {e}"[:500]
            log.exception("%s / %s failed", pair.search.title, mp)
            if not ctx.options.dry_run:
                await ctx.store.save_state(pair.search.id, mp, {
                    "consecutive_errors": pair.state.consecutive_errors + 1,
                    "last_error": msg,
                    "last_polled_at": utcnow(),  # don't hot-loop a broken pair
                })
                await ctx.store.update_marketplace(mp, {"last_error": msg, "last_error_at": utcnow()})
    summary["requests"] = limiter.used
    status = ctx.marketplaces.get(mp)
    if summary["done"] and status and status.consecutive_failures and not ctx.options.dry_run:
        await ctx.store.update_marketplace(mp, {"consecutive_failures": 0})
    return summary


# ---------------------------------------------------------------------------
# Whole run
# ---------------------------------------------------------------------------


async def run(
    settings: Settings,
    store: Any,
    adapters: dict[str, MarketplaceAdapter],
    options: RunOptions,
    telegram: Telegram | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    started_mono = time.monotonic()
    deadline = started_mono + options.max_runtime
    started = utcnow()
    if not options.dry_run:
        await store.update_heartbeat({"last_started_at": started, "last_status": "running"})

    summary: dict[str, Any] = {"marketplaces": {}}
    status = "ok"
    try:
        if telegram and not options.dry_run:
            try:
                summary["telegram_linked"] = await link_telegram_accounts(telegram, store)
            except Exception:
                log.exception("telegram linking failed")

        searches = await store.load_searches()
        states = await store.load_states()
        marketplaces = await store.load_marketplaces()
        blocklist = Blocklist(await store.load_blocklist())
        matcher = Matcher(load_brands(), blocklist, settings.spam_brand_threshold)
        now = utcnow()
        queues = build_queues(
            searches, states, marketplaces, now, settings.target_intervals,
            only_marketplace=options.only_marketplace, only_search=options.only_search,
            force=options.force,
        )
        queues = {mp: q for mp, q in queues.items() if mp in adapters}
        for mp, st in marketplaces.items():
            if st.paused_until and st.paused_until > now:
                summary["marketplaces"][mp] = {"status": f"paused until {st.paused_until.isoformat()}"}

        ctx = RunContext(settings, store, adapters, matcher, telegram, options, marketplaces, deadline)
        budget = default_budget(options.max_runtime, settings.request_interval, settings.jitter_max)
        own_client = http_client is None
        client = http_client or httpx.AsyncClient(timeout=20, follow_redirects=False)
        try:
            results = await asyncio.gather(
                *(run_marketplace(ctx, mp, q, client, budget) for mp, q in queues.items()),
                return_exceptions=True,
            )
        finally:
            if own_client:
                await client.aclose()
        for mp, res in zip(queues, results, strict=True):
            if isinstance(res, BaseException):
                status = "error"
                summary["marketplaces"][mp] = {"status": f"crashed: {res!r}"[:300]}
                log.error("marketplace %s crashed", mp, exc_info=res)
            else:
                summary["marketplaces"][mp] = res
                if res.get("errors"):
                    status = "partial" if status == "ok" else status

        profiles = await store.load_profiles()
        summary["notify"] = await send_pending(
            telegram, store, profiles, settings, dry_run=options.dry_run
        )
    except Exception as e:
        status = "error"
        summary["error"] = f"{type(e).__name__}: {e}"[:500]
        log.exception("run failed")
    finally:
        finished = utcnow()
        summary["runtime_s"] = round(time.monotonic() - started_mono, 1)
        if not options.dry_run:
            fields: dict[str, Any] = {"last_finished_at": finished, "last_status": status, "summary": summary}
            if status in ("ok", "partial"):
                fields["last_success_at"] = finished
            try:
                await store.update_heartbeat(fields)
            except Exception:
                log.exception("heartbeat write failed")
            if status == "error" and telegram:
                await telegram.admin(f"❌ Sourcer run failed: {summary.get('error') or summary}")
    summary["status"] = status
    return summary
