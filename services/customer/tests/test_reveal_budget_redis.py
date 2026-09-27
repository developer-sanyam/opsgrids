"""The reveal budget's script against a **real Redis**, and the fake the other tests use against it.

`services._REVEAL_BUDGET_SCRIPT` is Lua, and the suite's `FakeRedis` cannot run Lua: it runs a
Python equivalent. So two things are pinned here that the fake alone cannot prove:

* the script itself does what the service relies on — both windows spent together or neither, an
  expiry on every counter, a counter that lost its expiry given one — and a cancelled call, at any
  instant, never leaves a counter without one (second review, m2); and
* the fake agrees with the script, step for step, so the HTTP-level tests that use it are testing
  the real behaviour.

Needs a Redis at `REDIS_URL` (CI runs one, like the rest of the platform's integration tests) and
skips, saying so, when there is none.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app import services
from app.config import settings
from platform_core import cache
from platform_core.auth import Scope, ScopeContext
from platform_core.errors import RateLimitedProblem
from tests.helpers import FakeRedis, principal_for

pytestmark = pytest.mark.integration

PERMISSION = "crm.book.contact.read_sensitive"
DAY = 86_400


@pytest_asyncio.fixture
async def real(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Redis]:
    client = Redis.from_url(settings().redis_url, decode_responses=True)
    try:
        await asyncio.wait_for(client.ping(), timeout=2)
    except Exception:
        await client.aclose()
        pytest.skip("no Redis reachable at REDIS_URL: the reveal budget script was not exercised")
    monkeypatch.setattr(cache, "_redis", client)
    org = uuid.uuid4()
    client.test_org = org  # type: ignore[attr-defined]
    yield client
    async for key in client.scan_iter(match=f"rl:crm.reveal.*:{org}:*"):
        await client.delete(key)
    await client.aclose()


def _ctx(org: uuid.UUID, user: uuid.UUID | None = None) -> ScopeContext:
    principal = principal_for(org, [f"{PERMISSION}:ORG"], sub=user, amr=["otp", "stepup"])
    return ScopeContext(principal=principal, scope=Scope.ORG, permission=PERMISSION)


async def _keys(client: Redis, org: uuid.UUID) -> dict[str, tuple[int, int]]:
    """`{key: (count, ttl)}` for every reveal counter the organization has. TTL is -1 for none."""
    found: dict[str, tuple[int, int]] = {}
    async for key in client.scan_iter(match=f"rl:crm.reveal.*:{org}:*"):
        found[key] = (int(await client.get(key)), await client.ttl(key))
    return found


def _tighten(monkeypatch: pytest.MonkeyPatch, *, burst: int, daily: int) -> None:
    cfg = settings()
    monkeypatch.setattr(cfg, "reveal_burst_limit", burst)
    monkeypatch.setattr(cfg, "reveal_daily_limit", daily)


class TestTheScriptOnARealServer:
    async def test_it_spends_both_windows_and_gives_each_an_expiry(self, real: Redis) -> None:
        org = real.test_org  # type: ignore[attr-defined]
        await services.enforce_reveal_budget(_ctx(org))

        counters = await _keys(real, org)

        assert sorted(k.split(":")[1] for k in counters) == ["crm.reveal.burst", "crm.reveal.daily"]
        for key, (count, ttl) in counters.items():
            assert count == 1, key
            window = settings().reveal_burst_window_seconds if "burst" in key else DAY
            assert 0 < ttl <= 2 * window, f"{key} has ttl {ttl}"

    async def test_it_refuses_at_the_limit_and_a_refusal_spends_nothing(
        self, real: Redis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _tighten(monkeypatch, burst=3, daily=100)
        org = real.test_org  # type: ignore[attr-defined]
        ctx = _ctx(org)
        for _ in range(3):
            await services.enforce_reveal_budget(ctx)

        for _ in range(20):
            with pytest.raises(RateLimitedProblem) as refused:
                await services.enforce_reveal_budget(ctx)
            assert refused.value.headers == {"Retry-After": "600"}

        counts = {k.split(":")[1]: c for k, (c, _t) in (await _keys(real, org)).items()}
        assert counts == {"crm.reveal.burst": 3, "crm.reveal.daily": 3}

    async def test_the_daily_limit_refuses_with_the_daily_window_and_spends_no_burst(
        self, real: Redis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _tighten(monkeypatch, burst=100, daily=2)
        org = real.test_org  # type: ignore[attr-defined]
        ctx = _ctx(org)
        for _ in range(2):
            await services.enforce_reveal_budget(ctx)

        for _ in range(10):
            with pytest.raises(RateLimitedProblem) as refused:
                await services.enforce_reveal_budget(ctx)
            assert refused.value.headers == {"Retry-After": str(DAY)}

        counts = {k.split(":")[1]: c for k, (c, _t) in (await _keys(real, org)).items()}
        assert counts == {"crm.reveal.burst": 2, "crm.reveal.daily": 2}

    async def test_a_counter_that_lost_its_expiry_is_given_one(self, real: Redis) -> None:
        org = real.test_org  # type: ignore[attr-defined]
        ctx = _ctx(org)
        orphan = f"rl:crm.reveal.daily:{org}:{ctx.principal.sub}:{int(time.time()) // DAY}"
        await real.set(orphan, 1)  # no expiry: what an interrupted INCR/EXPIRE used to leave
        assert await real.ttl(orphan) == -1

        await services.enforce_reveal_budget(ctx)

        assert 0 < await real.ttl(orphan) <= 2 * DAY
        assert int(await real.get(orphan)) == 2

    async def test_a_call_cancelled_at_any_instant_never_leaves_a_counter_without_an_expiry(
        self, real: Redis
    ) -> None:
        """Second review, m2, on the real thing. Each call is cut off after a random few
        milliseconds — before it is sent, while it is in flight, after the server has run it — and
        whichever it was, no counter exists without an expiry. (The old INCR-then-EXPIRE could be
        cut between the two.)"""
        org = real.test_org  # type: ignore[attr-defined]
        rng = random.Random(20260927)  # noqa: S311 - a test's jitter, not a secret
        cut_off = 0
        for _ in range(400):
            ctx = _ctx(org, uuid.uuid4())
            try:
                await asyncio.wait_for(
                    services.enforce_reveal_budget(ctx), timeout=rng.uniform(0, 0.004)
                )
            except TimeoutError:
                cut_off += 1

        counters = await _keys(real, org)
        assert counters, "nothing reached the server: the test proved nothing"
        assert cut_off > 0, "nothing was cut off: the test proved nothing"
        assert all(ttl > 0 for _count, ttl in counters.values()), [
            key for key, (_c, ttl) in counters.items() if ttl <= 0
        ]


class TestTheFakeAgreesWithTheScript:
    """The HTTP-level tests run the fake. Replay the same calls through both and compare."""

    async def _both(self, real: Redis, fake: FakeRedis, keys: tuple[str, str], *args: int) -> None:
        wanted = await real.eval(services._REVEAL_BUDGET_SCRIPT, 2, *keys, *args)
        got = await fake.eval(services._REVEAL_BUDGET_SCRIPT, 2, *keys, *args)
        assert list(got) == list(wanted), (args, got, wanted)

    async def test_step_for_step_across_limits_that_change_under_it(self, real: Redis) -> None:
        org = real.test_org  # type: ignore[attr-defined]
        fake = FakeRedis()
        keys = (f"rl:crm.reveal.burst:{org}:agree:1", f"rl:crm.reveal.daily:{org}:agree:1")
        # burst 4 / daily 6: the burst is spent first; then a lower daily limit (3) refuses on the
        # day instead; then raised limits let it spend again.
        for _ in range(7):
            await self._both(real, fake, keys, 4, 6, 1200, 2 * DAY)
        for _ in range(3):
            await self._both(real, fake, keys, 100, 3, 1200, 2 * DAY)
        for _ in range(4):
            await self._both(real, fake, keys, 100, 100, 1200, 2 * DAY)
        assert {k: c for k, (c, _t) in (await _keys(real, org)).items()} == fake.store

    async def test_an_expiry_is_set_exactly_where_the_fake_says_it_is(self, real: Redis) -> None:
        org = real.test_org  # type: ignore[attr-defined]
        fake = FakeRedis()
        keys = (f"rl:crm.reveal.burst:{org}:ttl:1", f"rl:crm.reveal.daily:{org}:ttl:1")
        # A daily counter with no expiry, in both, before the first spend.
        await real.set(keys[1], 2)
        fake.store[keys[1]] = 2

        await self._both(real, fake, keys, 10, 10, 1200, 2 * DAY)

        assert set(fake.expiries) == set(keys)
        for key in keys:
            assert await real.ttl(key) > 0
