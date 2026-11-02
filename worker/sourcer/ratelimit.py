"""Per-marketplace rate limiting, retry and backoff (README 4.3)."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TypeVar

import httpx

T = TypeVar("T")


class MarketplaceBlocked(Exception):
    """429 / 403 / captcha: stop using this marketplace for the run and pause it."""


class TransientError(Exception):
    """5xx: retry once, then give up for this pair."""


class BudgetExhausted(Exception):
    """The marketplace's per-run request budget or the runtime cap is used up."""


class RateLimiter:
    """At least `interval` + random(0..jitter) seconds between requests.

    One instance per marketplace. Requests within a marketplace are sequential,
    so a simple 'next allowed time' is enough.
    """

    def __init__(
        self,
        interval: float,
        jitter: float,
        budget: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ):
        self.interval = interval
        self.jitter = jitter
        self.budget = budget
        self.used = 0
        self._clock = clock
        self._sleep = sleep
        self._rng = rng
        self._last: float | None = None

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)

    async def acquire(self, deadline: float | None = None) -> None:
        if self.used >= self.budget:
            raise BudgetExhausted("request budget used up")
        if self._last is not None:
            wait = self._last + self.interval + self._rng() * self.jitter - self._clock()
            if deadline is not None and self._clock() + max(wait, 0) > deadline:
                raise BudgetExhausted("runtime cap reached")
            if wait > 0:
                await self._sleep(wait)
        elif deadline is not None and self._clock() > deadline:
            raise BudgetExhausted("runtime cap reached")
        self.used += 1
        self._last = self._clock()


def next_backoff(consecutive_failures: int, base: timedelta, cap: timedelta) -> timedelta:
    """1h, 2h, 4h, ... capped at 24h. `consecutive_failures` counts *previous* failures."""
    return min(base * (2 ** max(consecutive_failures, 0)), cap)


async def call_with_retry(
    limiter: RateLimiter,
    fn: Callable[[], Awaitable[T]],
    *,
    retry_delay: float,
    deadline: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Rate-limited call. 5xx / network errors are retried once after `retry_delay`.

    MarketplaceBlocked and BudgetExhausted propagate immediately (no retry).
    """
    await limiter.acquire(deadline)
    try:
        return await fn()
    except (TransientError, httpx.TransportError):
        await sleep(retry_delay)
        await limiter.acquire(deadline)
        return await fn()


def default_budget(max_runtime: float, interval: float, jitter: float) -> int:
    """How many requests fit in one run at the average spacing."""
    spacing = interval + jitter / 2
    return 1_000_000 if spacing <= 0 else max(1, int(max_runtime // spacing))
