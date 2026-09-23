"""`reserve_once` — the "valid once" primitive behind auth's admin-gateway hop nonce check (m2 of
the third-round security review). Two properties carry the weight: the second claim on a key is
refused, and an unreachable Redis is an error rather than "not seen before"."""

from __future__ import annotations

from typing import Any

import pytest

from platform_core import cache
from platform_core.cache import reserve_once
from platform_core.errors import UpstreamUnavailableProblem


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(
        self, key: str, value: str, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True


@pytest.fixture
def redis() -> Any:
    fake = _FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


async def test_the_first_claim_wins_and_the_second_is_refused(redis: _FakeRedis) -> None:
    assert await reserve_once("nonce:abc", ttl=60) is True
    assert await reserve_once("nonce:abc", ttl=60) is False


async def test_different_keys_do_not_collide(redis: _FakeRedis) -> None:
    assert await reserve_once("nonce:a", ttl=60) is True
    assert await reserve_once("nonce:b", ttl=60) is True


async def test_the_reservation_carries_the_ttl(redis: _FakeRedis) -> None:
    await reserve_once("nonce:ttl", ttl=60)
    assert redis.ttls["nonce:ttl"] == 60


async def test_an_unreachable_redis_fails_closed() -> None:
    class _Down:
        async def set(self, *args: object, **kwargs: object) -> None:
            raise ConnectionError("redis is down")

    cache._redis = _Down()  # type: ignore[assignment]
    try:
        with pytest.raises(UpstreamUnavailableProblem):
            await reserve_once("nonce:x", ttl=60)
    finally:
        cache._redis = None
