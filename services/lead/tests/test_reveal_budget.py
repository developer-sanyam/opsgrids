"""The reveal route's brakes: a per-user budget, and a log line for every refusal.

`POST /leads/{id}:reveal` used to be limited only by the gateway's 300 requests a minute, and one
stepped-up token lives about three minutes — long enough to open hundreds of leads. It now also
spends from a budget keyed on the organization *and* the user (30 per 10 minutes, 200 per day),
held in Redis, and it **fails closed**: with Redis unreachable nothing is revealed.

The budget is spent when a value is about to be opened — after the lead has been found, shown to
be in scope and to have a value on file, before it is decrypted — so a request that was going to be
refused spends nothing, and a stolen token cannot exhaust the real person's budget with garbage.

Refusals write no audit row (an audit row means a value was opened) but each logs one WARNING,
so somebody walking lead ids shows up in the logs. `structlog.testing.capture_logs` is the
capture mechanism: this service logs through `platform_core.logging.get_logger` (structlog), not
stdlib `logging`, so `caplog` sees nothing from it.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import func, select
from structlog.testing import capture_logs

from app.config import settings
from app.main import app
from app.models import AuditLog
from platform_core.auth import Principal, get_principal
from tests.helpers import STEPPED_UP, FakeRedis, principal_for

pytestmark = pytest.mark.integration

REVEAL = "crm.lead.lead.read_sensitive"
RAW_PHONE = "+919876543210"
RAW_EMAIL = "priya@stellar.example"
REFUSED = "lead.reveal.refused"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _path(lead_id: uuid.UUID | str) -> str:
    return f"/api/v1/leads/{lead_id}:reveal"


def _revealer(
    org_id: uuid.UUID, *, sub: uuid.UUID | None = None, scope: str = "ORG", subject_ref=None
) -> Principal:
    return principal_for(
        org_id,
        [f"{REVEAL}:{scope}"],
        sub=sub,
        subject_ref=subject_ref,
        amr=STEPPED_UP,
        session_id="sid-under-test",
    )


def _tighten(monkeypatch: pytest.MonkeyPatch, *, burst: int = 100, daily: int = 1000) -> None:
    """Small budgets, so a test spends one in a handful of requests rather than thirty."""
    cfg = settings()
    monkeypatch.setattr(cfg, "reveal_burst_limit", burst)
    monkeypatch.setattr(cfg, "reveal_daily_limit", daily)


async def _committed_reads(admin_session, org_id: uuid.UUID) -> int:
    async with admin_session.begin():
        return (
            await admin_session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.organization_id == org_id,
                    AuditLog.action == "entity.sensitive_read",
                )
            )
        ).scalar_one()


def _refusals(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e.get("event") == REFUSED]


# --------------------------------------------------------------------- the budget


class TestRevealBudget:
    def test_the_defaults_are_thirty_per_ten_minutes_and_two_hundred_a_day(self) -> None:
        cfg = settings()
        assert cfg.reveal_burst_limit == 30
        assert cfg.reveal_burst_window_seconds == 600
        assert cfg.reveal_daily_limit == 200

    async def test_the_burst_budget_refuses_the_reveal_after_it_with_a_429(
        self, client, org_id, data, admin_session, monkeypatch
    ) -> None:
        _tighten(monkeypatch, burst=3)
        principal = _revealer(org_id)
        with as_principal(principal):
            for _ in range(3):
                ok = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
                assert ok.status_code == 200
            over = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})

        assert over.status_code == 429
        assert over.headers["content-type"].startswith("application/problem+json")
        body = over.json()
        assert body["status"] == 429
        assert body["title"] == "Too many requests"
        assert body["type"].endswith("/rate-limited")
        assert body["detail"] == "Too many requests. Please slow down."
        assert over.headers["retry-after"] == "600"
        # A refused attempt discloses nothing and records no read.
        assert RAW_PHONE not in over.text
        assert RAW_PHONE.removeprefix("+") not in over.text
        assert await _committed_reads(admin_session, org_id) == 3

    async def test_the_daily_budget_refuses_when_the_day_is_spent(
        self, client, org_id, data, monkeypatch
    ) -> None:
        _tighten(monkeypatch, burst=100, daily=2)
        with as_principal(_revealer(org_id)):
            for field in ("phone", "email"):
                ok = await client.post(_path(data["lead_a_id"]), json={"field": field})
                assert ok.status_code == 200
            over = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})

        assert over.status_code == 429
        assert over.headers["content-type"].startswith("application/problem+json")
        assert over.headers["retry-after"] == "86400"
        assert RAW_PHONE not in over.text

    async def test_the_counter_is_per_user(self, client, org_id, data, monkeypatch) -> None:
        """One person spending their budget does not spend a colleague's."""
        _tighten(monkeypatch, burst=2)
        spender = _revealer(org_id)
        colleague = _revealer(org_id)
        assert spender.sub != colleague.sub

        with as_principal(spender):
            for _ in range(2):
                assert (
                    await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
                ).status_code == 200
            assert (
                await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
            ).status_code == 429
        with as_principal(colleague):
            fresh = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
        assert fresh.status_code == 200

    async def test_the_counter_is_per_organization(
        self, org_id, redis: FakeRedis, monkeypatch
    ) -> None:
        """The same person in a second organization (a membership per organization) has a budget
        of their own there: spending it in one leaves the other untouched. Called on the service
        directly — a reveal in another organization needs a lead there, and this suite seeds one
        tenant."""
        from app import services
        from platform_core.auth import Scope, ScopeContext
        from platform_core.errors import RateLimitedProblem

        _tighten(monkeypatch, burst=2)
        user = uuid.uuid4()
        other_org = uuid.uuid4()

        def _ctx(organization: uuid.UUID) -> ScopeContext:
            principal = _revealer(organization, sub=user)
            return ScopeContext(principal=principal, scope=Scope.ORG, permission=REVEAL)

        for _ in range(2):
            await services.enforce_reveal_budget(_ctx(org_id))
        with pytest.raises(RateLimitedProblem):
            await services.enforce_reveal_budget(_ctx(org_id))
        await services.enforce_reveal_budget(_ctx(other_org))  # untouched

        burst_keys = [k for k in redis.store if k.startswith("rl:crm.reveal.burst:")]
        assert any(f":{org_id}:{user}:" in k for k in burst_keys)
        assert any(f":{other_org}:{user}:" in k for k in burst_keys)

    async def test_a_refused_request_spends_nothing(
        self, client, org_id, data, redis: FakeRedis, admin_session
    ) -> None:
        """The budget is spent when a value is about to be opened. Every way a request can be
        refused before that — an id that does not exist or is another organization's, a lead out
        of the caller's scope, a field the lead has no value for, a path or body that does not
        parse — leaves the counters exactly where they were, and writes no audit row."""
        own_only = _revealer(org_id, scope="OWN", subject_ref=data["owner_b"])
        with as_principal(_revealer(org_id)):
            unknown = await client.post(_path(uuid.uuid4()), json={"field": "phone"})
            nothing_on_file = await client.post(_path(data["lead_b_id"]), json={"field": "email"})
            bad_field = await client.post(_path(data["lead_a_id"]), json={"field": "notes"})
            bad_path = await client.post("/api/v1/leads/not-a-uuid:reveal", json={"field": "phone"})
        with as_principal(own_only):
            out_of_scope = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
        with as_principal(_revealer(uuid.uuid4())):
            foreign = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})

        assert [
            r.status_code
            for r in (unknown, nothing_on_file, bad_field, bad_path, out_of_scope, foreign)
        ] == [404, 404, 422, 422, 403, 404]
        assert redis.store == {}
        assert await _committed_reads(admin_session, org_id) == 0

    async def test_a_token_cannot_burn_the_real_persons_budget_with_refused_requests(
        self, client, org_id, data, monkeypatch
    ) -> None:
        """The abuse the ordering closes: whoever holds the token sends a run of requests that
        were never going to reveal anything, and the person it belongs to is then told their budget
        is spent. Now the run costs them nothing, and their own reveal still works."""
        _tighten(monkeypatch, burst=3)
        principal = _revealer(org_id)
        with as_principal(principal):
            for _ in range(25):
                assert (
                    await client.post(_path(uuid.uuid4()), json={"field": "phone"})
                ).status_code == 404
            revealed = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
        assert revealed.status_code == 200

    async def test_only_a_request_that_reveals_something_is_counted(
        self, client, org_id, data, monkeypatch
    ) -> None:
        _tighten(monkeypatch, burst=2)
        with as_principal(_revealer(org_id)):
            await client.post(_path(uuid.uuid4()), json={"field": "phone"})  # refused: free
            for _ in range(2):
                assert (
                    await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
                ).status_code == 200
            over = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
        assert over.status_code == 429

    async def test_a_caller_who_may_not_reveal_spends_nothing(
        self, client, org_id, data, redis: FakeRedis
    ) -> None:
        """The budget follows the permission and the step-up, so an unauthorised caller neither
        spends anything nor reaches Redis at all."""
        no_permission = principal_for(org_id, [], amr=STEPPED_UP)
        no_step_up = principal_for(org_id, [f"{REVEAL}:ORG"])
        for principal in (no_permission, no_step_up):
            with as_principal(principal):
                response = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
            assert response.status_code == 403
        assert redis.store == {}

    async def test_the_budget_is_spent_after_the_lookup_and_before_the_decrypt(
        self, client, org_id, data, monkeypatch
    ) -> None:
        """The order is the property: cheap checks first (the lead exists, is in scope and has a
        value), then the budget, then — only then — the value is opened."""
        from app import services

        order: list[str] = []
        real_get, real_budget, real_open = (
            services.selectors.get_lead,
            services.enforce_reveal_budget,
            services.open_sealed,
        )

        async def _get(*args, **kwargs):  # type: ignore[no-untyped-def]
            order.append("lookup")
            return await real_get(*args, **kwargs)

        async def _budget(*args, **kwargs):  # type: ignore[no-untyped-def]
            order.append("budget")
            return await real_budget(*args, **kwargs)

        def _open(*args, **kwargs):  # type: ignore[no-untyped-def]
            order.append("decrypt")
            return real_open(*args, **kwargs)

        monkeypatch.setattr(services.selectors, "get_lead", _get)
        monkeypatch.setattr(services, "enforce_reveal_budget", _budget)
        monkeypatch.setattr(services, "open_sealed", _open)

        with as_principal(_revealer(org_id)):
            response = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})

        assert response.status_code == 200
        assert order == ["lookup", "budget", "decrypt"]

    def test_the_guard_does_not_spend_the_budget(self) -> None:
        """The dependency that runs ahead of the route body only decides authorization. Were it to
        spend the budget again, a refused request would be counted before it is known to be one."""
        import inspect

        from app.api import routes

        assert "enforce_reveal_budget" not in inspect.getsource(routes._reveal_guard)

    async def test_a_redis_that_never_answers_is_the_same_503_and_is_not_waited_for(
        self, client, org_id, data, admin_session, monkeypatch, redis: FakeRedis
    ) -> None:
        """The budget is spent inside the request's database transaction, so a Redis that neither
        answers nor refuses must not hold the connection until some socket timeout: it is bounded,
        and over the bound it is an outage — nothing revealed, nothing recorded."""
        import asyncio

        from app import services

        redis.hang_on = 1
        monkeypatch.setattr(services, "_REVEAL_BUDGET_TIMEOUT_SECONDS", 0.05)

        with as_principal(_revealer(org_id)):
            response = await asyncio.wait_for(
                client.post(_path(data["lead_a_id"]), json={"field": "phone"}), timeout=5
            )

        assert response.status_code == 503
        assert RAW_PHONE not in response.text
        assert await _committed_reads(admin_session, org_id) == 0

    @pytest.mark.parametrize("server_ran_it", [False, True])
    @pytest.mark.parametrize("hang_on", [1, 2, 3])
    async def test_a_timeout_at_any_point_never_leaves_a_counter_without_an_expiry(
        self,
        client,
        org_id,
        data,
        admin_session,
        monkeypatch,
        redis: FakeRedis,
        hang_on,
        server_ran_it,
    ) -> None:
        """Second review, m2. The check used to be an `INCR` and then an `EXPIRE` per window, so a
        timeout between the two left a counter that never expired — for the daily window, a person
        refused for ever. Now there is one round trip, and one that timed out either never reached
        the server or ran there whole. Whichever round trip hangs, and whether or not the server had
        already run it, every counter that exists has an expiry."""
        import asyncio

        from app import services

        redis.hang_on = hang_on
        redis.hang_after_apply = server_ran_it
        monkeypatch.setattr(services, "_REVEAL_BUDGET_TIMEOUT_SECONDS", 0.05)

        with as_principal(_revealer(org_id)):
            response = await asyncio.wait_for(
                client.post(_path(data["lead_a_id"]), json={"field": "phone"}), timeout=5
            )

        assert set(redis.store) <= set(redis.expiries), "a counter was left with no expiry"
        assert redis.round_trips == 1, "the budget check is one round trip"
        if hang_on == 1:
            assert response.status_code == 503
            assert RAW_PHONE not in response.text
            assert await _committed_reads(admin_session, org_id) == 0
        else:
            assert response.status_code == 200  # the hang was never reached

    async def test_the_check_is_one_round_trip_bounded_by_one_timeout(
        self, client, org_id, data, monkeypatch, redis: FakeRedis
    ) -> None:
        """Both windows are spent in a single step, so the worst case for a request is one timeout,
        not one per window: a Redis that answers slower than the bound is a 503 after the bound,
        and a healthy one is asked exactly once."""
        import asyncio
        import time

        from app import services

        with as_principal(_revealer(org_id)):
            ok = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
            assert ok.status_code == 200
            assert redis.round_trips == 1
            assert len(redis.store) == 2  # both windows, spent together

            redis.hang_on = 2
            monkeypatch.setattr(services, "_REVEAL_BUDGET_TIMEOUT_SECONDS", 0.3)
            started = time.monotonic()
            slow = await asyncio.wait_for(
                client.post(_path(data["lead_a_id"]), json={"field": "phone"}), timeout=5
            )
            elapsed = time.monotonic() - started

        assert slow.status_code == 503
        assert elapsed < 0.6, f"took {elapsed:.2f}s: more than one timeout was waited"

    async def test_a_request_refused_for_the_burst_does_not_spend_the_daily_budget(
        self, client, org_id, data, redis: FakeRedis, monkeypatch
    ) -> None:
        """A refusal spends nothing. Otherwise a stolen token, having spent the burst, could burn
        the real person's day by hammering: each refused request would still count against it."""
        _tighten(monkeypatch, burst=3, daily=1000)
        with as_principal(_revealer(org_id)):
            for _ in range(3):
                assert (
                    await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
                ).status_code == 200
            for _ in range(25):
                assert (
                    await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
                ).status_code == 429

        by_prefix = {key.split(":")[1]: count for key, count in redis.store.items()}
        assert by_prefix == {"crm.reveal.burst": 3, "crm.reveal.daily": 3}

    async def test_a_request_refused_for_the_day_does_not_spend_the_burst(
        self, client, org_id, data, redis: FakeRedis, monkeypatch
    ) -> None:
        _tighten(monkeypatch, burst=1000, daily=2)
        with as_principal(_revealer(org_id)):
            for _ in range(2):
                await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
            for _ in range(10):
                refused = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
                assert refused.status_code == 429
                assert refused.headers["retry-after"] == "86400"

        by_prefix = {key.split(":")[1]: count for key, count in redis.store.items()}
        assert by_prefix == {"crm.reveal.burst": 2, "crm.reveal.daily": 2}

    async def test_a_counter_that_somehow_lost_its_expiry_is_given_one_by_the_next_request(
        self, client, org_id, data, redis: FakeRedis
    ) -> None:
        import time

        principal = _revealer(org_id)
        who = f"{org_id}:{principal.sub}"
        now = int(time.time())
        orphan = f"rl:crm.reveal.daily:{who}:{now // 86_400}"
        redis.store[orphan] = 1  # a counter with no expiry
        assert orphan not in redis.expiries

        with as_principal(principal):
            response = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})

        assert response.status_code == 200
        assert redis.expiries[orphan] == 2 * 86_400
        assert redis.store[orphan] == 2

    async def test_the_keys_carry_ids_and_nothing_personal(
        self, client, org_id, data, redis: FakeRedis
    ) -> None:
        principal = _revealer(org_id)
        with as_principal(principal):
            await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
        assert len(redis.store) == 2  # the burst window and the daily window
        for key in redis.store:
            assert f":{org_id}:{principal.sub}:" in key
            assert RAW_PHONE.removeprefix("+") not in key
            assert RAW_EMAIL not in key
            assert str(data["lead_a_id"]) not in key


# --------------------------------------------------------------------- a Redis outage


class TestRedisOutage:
    """What an unreachable Redis does to this service, precisely: reveals stop (503, nothing
    opened, nothing audited), readiness reports it, and every other route carries on."""

    async def test_reveals_fail_closed_and_open_nothing(
        self, client, org_id, data, redis: FakeRedis, admin_session
    ) -> None:
        redis.poisoned = True
        with as_principal(_revealer(org_id)):
            response = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})

        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["status"] == 503
        assert RAW_PHONE not in response.text
        assert RAW_PHONE.removeprefix("+") not in response.text
        assert await _committed_reads(admin_session, org_id) == 0

    async def test_no_client_at_all_is_the_same_outage(
        self, client, org_id, data, monkeypatch
    ) -> None:
        """A process whose lifespan never opened Redis (`get_redis` raises) is refused the same
        way — never waved through."""
        from platform_core import cache

        monkeypatch.setattr(cache, "_redis", None)
        with as_principal(_revealer(org_id)):
            response = await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
        assert response.status_code == 503

    async def test_every_other_route_carries_on(
        self, client, org_id, data, redis: FakeRedis
    ) -> None:
        redis.poisoned = True
        reader = principal_for(org_id, ["crm.lead.lead.read:ORG"])
        with as_principal(reader):
            listed = await client.get("/api/v1/leads")
            one = await client.get(f"/api/v1/leads/{data['lead_a_id']}")
        assert listed.status_code == 200
        assert one.status_code == 200
        assert (await client.get("/health")).status_code == 200  # liveness: no restart

    async def test_readiness_reports_redis_and_answers_200_either_way(
        self, client, redis: FakeRedis
    ) -> None:
        """`/health/ready` names Redis now. It answers HTTP 200 whatever the body says (the
        platform's rule — `tools/smoke.py` and the ops monitor read the *body*), so a Redis
        outage shows as `status: degraded`, never as a failed probe that restarts the process."""
        up = await client.get("/health/ready")
        assert up.status_code == 200
        assert up.json()["checks"]["redis"] == "ok"

        redis.poisoned = True
        down = await client.get("/health/ready")
        assert down.status_code == 200
        body = down.json()
        assert body["status"] == "degraded"
        assert body["checks"]["redis"].startswith("error")


# --------------------------------------------------------------------- refusals are logged


class TestRefusedRevealsAreLogged:
    """One structured WARNING per refusal — never an audit row — naming who, where, which lead
    and why, and never a value."""

    async def _refused(self, client, principal: Principal, path: str, body: dict | None = None):
        with capture_logs() as entries, as_principal(principal):
            response = await client.post(path, json=body or {"field": "phone"})
        return response, _refusals(entries), entries

    def _assert_line(self, line: dict, *, org_id, principal: Principal, reason: str) -> None:
        assert line["log_level"] == "warning"
        assert line["reason"] == reason
        assert line["organization_id"] == str(org_id)
        assert line["actor_id"] == str(principal.sub)
        assert set(line) == {
            "event",
            "log_level",
            "reason",
            "organization_id",
            "actor_id",
            "lead_id",
            "field",
            "request_id",
        }
        blob = json.dumps(line, default=str)
        assert RAW_PHONE.removeprefix("+") not in blob
        assert RAW_EMAIL not in blob

    async def test_a_caller_without_the_permission(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["crm.lead.lead.read:ORG"], amr=STEPPED_UP)
        response, lines, _ = await self._refused(client, principal, _path(data["lead_a_id"]))

        assert response.status_code == 403
        (line,) = lines
        self._assert_line(line, org_id=org_id, principal=principal, reason="missing_permission")
        assert line["lead_id"] == str(data["lead_a_id"])
        assert line["request_id"] == response.headers["x-request-id"]

    async def test_a_caller_who_has_not_stepped_up(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [f"{REVEAL}:ORG"])
        response, lines, _ = await self._refused(client, principal, _path(data["lead_a_id"]))

        assert response.status_code == 403
        (line,) = lines
        self._assert_line(line, org_id=org_id, principal=principal, reason="step_up_required")
        assert line["lead_id"] == str(data["lead_a_id"])

    async def test_a_lead_outside_the_callers_scope(self, client, org_id, data) -> None:
        principal = _revealer(org_id, scope="OWN", subject_ref=data["owner_b"])
        response, lines, _ = await self._refused(client, principal, _path(data["lead_a_id"]))

        assert response.status_code == 403
        (line,) = lines
        self._assert_line(line, org_id=org_id, principal=principal, reason="out_of_scope")
        assert line["lead_id"] == str(data["lead_a_id"])
        assert line["field"] == "phone"

    async def test_a_lead_that_is_not_in_this_organization(self, client, org_id, data) -> None:
        principal = _revealer(uuid.uuid4())  # another tenant's caller, this tenant's lead
        response, lines, _ = await self._refused(client, principal, _path(data["lead_a_id"]))

        assert response.status_code == 404
        (line,) = lines
        self._assert_line(
            line, org_id=principal.organization_id, principal=principal, reason="lead_not_found"
        )
        assert line["lead_id"] == str(data["lead_a_id"])

    async def test_a_lead_with_nothing_on_file(self, client, org_id, data) -> None:
        principal = _revealer(org_id)
        response, lines, _ = await self._refused(
            client, principal, _path(data["lead_b_id"]), {"field": "email"}
        )

        assert response.status_code == 404
        (line,) = lines
        self._assert_line(line, org_id=org_id, principal=principal, reason="nothing_on_file")
        assert line["field"] == "email"

    async def test_a_spent_budget(self, client, org_id, data, monkeypatch) -> None:
        _tighten(monkeypatch, burst=1)
        principal = _revealer(org_id)
        with as_principal(principal):
            await client.post(_path(data["lead_a_id"]), json={"field": "phone"})
        response, lines, _ = await self._refused(client, principal, _path(data["lead_a_id"]))

        assert response.status_code == 429
        (line,) = lines
        self._assert_line(line, org_id=org_id, principal=principal, reason="rate_limited_burst")

    async def test_a_path_that_is_not_a_uuid_logs_no_lead_id(self, client, org_id) -> None:
        """The path parameter is client text: it is logged only once it parses as a UUID."""
        principal = principal_for(org_id, [], amr=STEPPED_UP)
        response, lines, _ = await self._refused(client, principal, "/api/v1/leads/<script>:reveal")

        assert response.status_code == 403
        (line,) = lines
        assert line["lead_id"] is None
        assert "<script>" not in json.dumps(line)

    async def test_a_permitted_reveal_logs_no_refusal(self, client, org_id, data) -> None:
        response, lines, entries = await self._refused(
            client, _revealer(org_id), _path(data["lead_a_id"])
        )
        assert response.status_code == 200
        assert lines == []
        assert any(e.get("event") == "lead.field_revealed" for e in entries)

    async def test_a_refusal_writes_no_audit_row(self, client, org_id, data, admin_session) -> None:
        for principal in (
            principal_for(org_id, [], amr=STEPPED_UP),
            _revealer(org_id, scope="OWN", subject_ref=data["owner_b"]),
        ):
            response, lines, _ = await self._refused(client, principal, _path(data["lead_a_id"]))
            assert response.status_code == 403
            assert len(lines) == 1
        assert await _committed_reads(admin_session, org_id) == 0
