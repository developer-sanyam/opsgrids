"""`check_rate_limit` / `record_rate_limit_hit` — the read-only twin of `enforce_rate_limit`.

The gateway's bucket of *rejected* webhook deliveries is read before a delivery is forwarded and
spent only after the service has answered, so it needs a read that costs nothing and a write that
cannot turn a handled response into an error. Three properties carry the weight: reading never
creates or increments anything; a spent window refuses with a 429 and a `Retry-After`; and the two
fail in opposite directions on purpose — the read closed (an unverifiable limit is not one to wave
through), the write open-and-logged (the service has already answered).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from platform_core import cache
from platform_core.cache import check_rate_limit, enforce_rate_limit, record_rate_limit_hit
from platform_core.errors import RateLimitedProblem, UpstreamUnavailableProblem


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> None:
        self.expiries[key] = seconds


@pytest.fixture
def redis() -> Any:
    fake = _FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


async def test_reading_an_untouched_bucket_passes_and_creates_nothing(redis: _FakeRedis) -> None:
    await check_rate_limit("hook-fail:x", limit=3)
    assert redis.store == {} and redis.expiries == {}


async def test_reading_never_spends_however_often_it_is_done(redis: _FakeRedis) -> None:
    for _ in range(50):
        await check_rate_limit("hook-fail:x", limit=1)
    assert redis.store == {}


async def test_the_bucket_refuses_once_it_holds_the_limit_and_not_before(redis: _FakeRedis) -> None:
    for _ in range(3):
        await check_rate_limit("hook-fail:x", limit=3)  # 0, 1 and 2 spent: still open
        await record_rate_limit_hit("hook-fail:x")
    with pytest.raises(RateLimitedProblem) as refused:
        await check_rate_limit("hook-fail:x", limit=3)
    assert refused.value.status_code == 429
    assert refused.value.headers == {"Retry-After": "60"}


async def test_the_refusal_says_what_the_caller_asked_it_to(redis: _FakeRedis) -> None:
    await record_rate_limit_hit("hook-fail:x")
    with pytest.raises(RateLimitedProblem, match="rejected deliveries"):
        await check_rate_limit("hook-fail:x", limit=1, detail="Too many rejected deliveries.")


async def test_recording_sets_the_expiry_once_on_the_first_hit(redis: _FakeRedis) -> None:
    await record_rate_limit_hit("hook-fail:x")
    key = next(iter(redis.store))
    assert redis.expiries == {key: 120}, "two windows of slack, like `enforce_rate_limit`"
    redis.expiries.clear()
    await record_rate_limit_hit("hook-fail:x")
    assert redis.expiries == {}, "only the first hit in a window sets it"


async def test_it_is_the_same_bucket_as_enforce_rate_limit_uses(redis: _FakeRedis) -> None:
    """One window key for both, so a bucket can be spent one way and read the other."""
    await enforce_rate_limit("shared", limit=10)
    await enforce_rate_limit("shared", limit=10)
    with pytest.raises(RateLimitedProblem):
        await check_rate_limit("shared", limit=2)
    key = next(iter(redis.store))
    assert key == f"rl:shared:{int(time.time()) // 60}"


async def test_a_new_window_starts_empty(
    redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    await record_rate_limit_hit("hook-fail:x")
    with pytest.raises(RateLimitedProblem):
        await check_rate_limit("hook-fail:x", limit=1)
    monkeypatch.setattr(time, "time", lambda: time.monotonic() + 10_000_000)
    await check_rate_limit("hook-fail:x", limit=1)


async def test_an_unreachable_redis_fails_the_read_closed() -> None:
    class _Down:
        async def get(self, key: str) -> None:
            raise ConnectionError("redis is down")

    cache._redis = _Down()  # type: ignore[assignment]
    try:
        with pytest.raises(UpstreamUnavailableProblem):
            await check_rate_limit("hook-fail:x", limit=1)
    finally:
        cache._redis = None


async def test_an_unreachable_redis_never_makes_the_write_raise() -> None:
    """The service has already answered; a counter that cannot be written is logged and the
    response stands."""

    class _Down:
        async def incr(self, key: str) -> int:
            raise ConnectionError("redis is down")

    cache._redis = _Down()  # type: ignore[assignment]
    try:
        await record_rate_limit_hit("hook-fail:x")
    finally:
        cache._redis = None
