from datetime import timedelta

import httpx
import pytest

from sourcer.ratelimit import (
    BudgetExhausted,
    MarketplaceBlocked,
    RateLimiter,
    TransientError,
    call_with_retry,
    default_budget,
    next_backoff,
)


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


async def test_limiter_spaces_requests_with_jitter():
    clock = FakeClock()
    lim = RateLimiter(6, 3, budget=10, clock=clock, sleep=clock.sleep, rng=lambda: 0.5)
    await lim.acquire()
    await lim.acquire()
    await lim.acquire()
    assert clock.sleeps == [7.5, 7.5]
    assert lim.used == 3


async def test_limiter_does_not_wait_if_time_passed():
    clock = FakeClock()
    lim = RateLimiter(6, 0, budget=10, clock=clock, sleep=clock.sleep)
    await lim.acquire()
    clock.t += 100
    await lim.acquire()
    assert clock.sleeps == []


async def test_budget_and_deadline():
    clock = FakeClock()
    lim = RateLimiter(6, 0, budget=1, clock=clock, sleep=clock.sleep)
    await lim.acquire()
    with pytest.raises(BudgetExhausted):
        await lim.acquire()
    lim2 = RateLimiter(6, 0, budget=10, clock=clock, sleep=clock.sleep)
    await lim2.acquire(deadline=clock.t + 5)
    with pytest.raises(BudgetExhausted):  # next slot would be after the deadline
        await lim2.acquire(deadline=clock.t + 5)


def test_backoff_doubles_and_caps():
    base, cap = timedelta(hours=1), timedelta(hours=24)
    assert [next_backoff(n, base, cap).total_seconds() / 3600 for n in range(7)] == [1, 2, 4, 8, 16, 24, 24]


def test_default_budget():
    assert default_budget(150, 6, 3) == 20
    assert default_budget(1, 6, 3) == 1


async def test_retry_once_on_transient_then_give_up():
    clock = FakeClock()
    lim = RateLimiter(0, 0, budget=10, clock=clock, sleep=clock.sleep)
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        raise TransientError("503")

    with pytest.raises(TransientError):
        await call_with_retry(lim, flaky, retry_delay=10, sleep=clock.sleep)
    assert calls == 2
    assert 10 in clock.sleeps


async def test_retry_succeeds_after_network_error():
    lim = RateLimiter(0, 0, budget=10)
    calls = 0

    async def once_broken():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("boom")
        return "ok"

    async def no_sleep(_):
        return None

    assert await call_with_retry(lim, once_broken, retry_delay=10, sleep=no_sleep) == "ok"


async def test_blocked_is_never_retried():
    lim = RateLimiter(0, 0, budget=10)
    calls = 0

    async def blocked():
        nonlocal calls
        calls += 1
        raise MarketplaceBlocked("429")

    with pytest.raises(MarketplaceBlocked):
        await call_with_retry(lim, blocked, retry_delay=0)
    assert calls == 1
