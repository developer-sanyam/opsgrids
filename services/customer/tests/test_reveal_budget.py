"""The contact-phone reveal's brakes: a per-user budget, `Cache-Control: no-store`, the session
that did it in the audit row, and a log line for every refusal.

`POST /contacts/{id}/phone:reveal` used to be limited only by the gateway's 300 requests a minute,
and one stepped-up token lives about three minutes — long enough to open hundreds of contacts. It
now also spends from a budget keyed on the organization *and* the user (30 per 10 minutes, 200 per
day), held in Redis and shared with lead-service's lead reveal, and it **fails closed**: with Redis
unreachable nothing is revealed.

Refusals write no audit row (an audit row means a value was opened) but each logs one WARNING, so
somebody walking contact ids shows up in the logs. `structlog.testing.capture_logs` is the capture
mechanism: this service logs through `platform_core.logging.get_logger` (structlog), not stdlib
`logging`, so `caplog` sees nothing from it.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select
from structlog.testing import capture_logs

from app import services
from app.config import settings
from app.main import app
from app.models import AuditLog
from platform_core import cache
from platform_core.auth import Principal, Scope, get_principal
from tests.helpers import STEPPED_UP, FakeRedis, ctx_for, principal_for

pytestmark = pytest.mark.integration

RAW = "+917976603446"
WRITE = "crm.book.contact.write"
SENSITIVE = "crm.book.contact.read_sensitive"
REFUSED = "contact.reveal.refused"
SESSION_ID = "e2a1f0c3-session-of-the-stepped-up-sign-in"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _path(contact_id: uuid.UUID | str) -> str:
    return f"/api/v1/contacts/{contact_id}/phone:reveal"


def _revealer(
    org_id: uuid.UUID, *, sub: uuid.UUID | None = None, scope: str = "ORG", subject_ref=None
) -> Principal:
    return principal_for(
        org_id,
        [f"{SENSITIVE}:{scope}"],
        sub=sub,
        subject_ref=subject_ref,
        amr=STEPPED_UP,
        session_id=SESSION_ID,
    )


def _tighten(monkeypatch: pytest.MonkeyPatch, *, burst: int = 100, daily: int = 1000) -> None:
    """Small budgets, so a test spends one in a handful of requests rather than thirty."""
    cfg = settings()
    monkeypatch.setattr(cfg, "reveal_burst_limit", burst)
    monkeypatch.setattr(cfg, "reveal_daily_limit", daily)


async def _seal_a_number(session, org_id: uuid.UUID, data: dict) -> None:
    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        await services.set_contact_phone(session, write, data["contact_a"], RAW)


async def _committed_reads(admin_session, org_id: uuid.UUID) -> list[AuditLog]:
    """The **committed** `entity.sensitive_read` rows, read on a separate BYPASSRLS connection so
    a row the request only staged and never committed is not in the result."""
    async with admin_session.begin():
        rows = (
            (
                await admin_session.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "entity.sensitive_read",
                    )
                    .order_by(AuditLog.at, AuditLog.id)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


def _refusals(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e.get("event") == REFUSED]


# --------------------------------------------------------------------- never cached


class TestNeverCached:
    async def test_a_revealed_number_is_sent_no_store(self, client, session, org_id, data) -> None:
        await _seal_a_number(session, org_id, data)
        with as_principal(_revealer(org_id)):
            response = await client.post(_path(data["contact_a_id"]))
        assert response.status_code == 200
        assert response.json()["phone"] == RAW
        assert response.headers["cache-control"] == "no-store"

    async def test_so_is_a_contact_with_no_number(self, client, org_id, data) -> None:
        with as_principal(_revealer(org_id)):
            response = await client.post(_path(data["contact_a_id"]))
        assert response.status_code == 200
        assert response.json()["phone"] is None
        assert response.headers["cache-control"] == "no-store"


# --------------------------------------------------------------------- the audit row's session


class TestAuditRowCarriesTheSession:
    async def test_the_row_has_the_sid_and_no_value(
        self, client, session, org_id, data, admin_session
    ) -> None:
        """The row can be tied to the sign-in (and so the MFA step-up) that authorised the
        reveal — `session_id`, the token's `sid` — and is still exactly the field's name, the
        session id and nothing else: no number, no mask, no contact detail."""
        await _seal_a_number(session, org_id, data)
        principal = _revealer(org_id)
        with as_principal(principal):
            response = await client.post(_path(data["contact_a_id"]))
        assert response.status_code == 200

        (row,) = await _committed_reads(admin_session, org_id)
        assert row.action == "entity.sensitive_read"
        assert row.entity == "contact"
        assert row.entity_id == data["contact_a_id"]
        assert row.actor_id == principal.sub
        assert row.after == {"fields": ["phone"], "session_id": SESSION_ID, "amr": STEPPED_UP}
        # A correlation aid only: the middleware adopts a client-supplied `X-Request-ID`, so this
        # is not evidence of anything. The session id above is.
        assert row.request_id
        blob = json.dumps([row.before, row.after, row.reason], default=str)
        assert RAW not in blob
        assert RAW.removeprefix("+") not in blob
        assert "7976603446" not in blob

    async def test_a_principal_with_no_sid_still_writes_the_row_with_a_null_one(
        self, client, org_id, data, admin_session
    ) -> None:
        principal = principal_for(org_id, [f"{SENSITIVE}:ORG"], amr=STEPPED_UP)
        with as_principal(principal):
            response = await client.post(_path(data["contact_a_id"]))
        assert response.status_code == 200

        (row,) = await _committed_reads(admin_session, org_id)
        assert row.after == {"fields": ["phone"], "session_id": None, "amr": STEPPED_UP}

    async def test_the_row_says_how_the_reveal_was_authorised(
        self, client, org_id, data, admin_session
    ) -> None:
        """The token's `amr` is recorded, so an auditor can tell a code proved for this reveal
        (`["pwd", "otp", "stepup"]`) from a session that merely gave one at sign-in — which the
        route now refuses, but which the row could not otherwise have told apart."""
        principal = principal_for(
            org_id, [f"{SENSITIVE}:ORG"], amr=["pwd", "otp", "stepup"], session_id=SESSION_ID
        )
        with as_principal(principal):
            response = await client.post(_path(data["contact_a_id"]))
        assert response.status_code == 200

        (row,) = await _committed_reads(admin_session, org_id)
        assert row.after["amr"] == ["pwd", "otp", "stepup"]
        assert set(row.after) == {"fields", "session_id", "amr"}


# --------------------------------------------------------------------- the budget


class TestRevealBudget:
    def test_the_defaults_are_thirty_per_ten_minutes_and_two_hundred_a_day(self) -> None:
        cfg = settings()
        assert cfg.reveal_burst_limit == 30
        assert cfg.reveal_burst_window_seconds == 600
        assert cfg.reveal_daily_limit == 200

    async def test_the_burst_budget_refuses_the_reveal_after_it_with_a_429(
        self, client, session, org_id, data, admin_session, monkeypatch
    ) -> None:
        await _seal_a_number(session, org_id, data)
        _tighten(monkeypatch, burst=3)
        with as_principal(_revealer(org_id)):
            for _ in range(3):
                assert (await client.post(_path(data["contact_a_id"]))).status_code == 200
            over = await client.post(_path(data["contact_a_id"]))

        assert over.status_code == 429
        assert over.headers["content-type"].startswith("application/problem+json")
        body = over.json()
        assert body["status"] == 429
        assert body["title"] == "Too many requests"
        assert body["type"].endswith("/rate-limited")
        assert body["detail"] == "Too many requests. Please slow down."
        assert over.headers["retry-after"] == "600"
        # A refused attempt discloses nothing and records no read.
        assert RAW not in over.text
        assert RAW.removeprefix("+") not in over.text
        assert len(await _committed_reads(admin_session, org_id)) == 3

    async def test_the_daily_budget_refuses_when_the_day_is_spent(
        self, client, org_id, data, monkeypatch
    ) -> None:
        _tighten(monkeypatch, burst=100, daily=2)
        with as_principal(_revealer(org_id)):
            for _ in range(2):
                assert (await client.post(_path(data["contact_a_id"]))).status_code == 200
            over = await client.post(_path(data["contact_a_id"]))

        assert over.status_code == 429
        assert over.headers["content-type"].startswith("application/problem+json")
        assert over.headers["retry-after"] == "86400"

    async def test_the_counter_is_per_user(self, client, org_id, data, monkeypatch) -> None:
        """One person spending their budget does not spend a colleague's."""
        _tighten(monkeypatch, burst=2)
        spender = _revealer(org_id)
        colleague = _revealer(org_id)
        assert spender.sub != colleague.sub

        with as_principal(spender):
            for _ in range(2):
                assert (await client.post(_path(data["contact_a_id"]))).status_code == 200
            assert (await client.post(_path(data["contact_a_id"]))).status_code == 429
        with as_principal(colleague):
            fresh = await client.post(_path(data["contact_a_id"]))
        assert fresh.status_code == 200

    async def test_the_counter_is_per_organization(
        self, org_id, redis: FakeRedis, monkeypatch
    ) -> None:
        """The same person in a second organization (a membership per organization) has a budget
        of their own there: spending it in one leaves the other untouched. Called on the service
        directly — a reveal in another organization needs a contact there, and this suite seeds
        one tenant."""
        from platform_core.auth import ScopeContext
        from platform_core.errors import RateLimitedProblem

        _tighten(monkeypatch, burst=2)
        user = uuid.uuid4()
        other_org = uuid.uuid4()

        def _ctx(organization: uuid.UUID) -> ScopeContext:
            principal = _revealer(organization, sub=user)
            return ScopeContext(principal=principal, scope=Scope.ORG, permission=SENSITIVE)

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
        """The budget is spent when a contact is about to be opened. Every way a request can be
        refused before that — an id that does not exist or is another organization's, a contact
        outside the caller's scope, a path that does not parse — leaves the counters where they
        were, and writes no audit row."""
        with as_principal(_revealer(org_id)):
            unknown = await client.post(_path(uuid.uuid4()))
            bad_path = await client.post("/api/v1/contacts/not-a-uuid/phone:reveal")
        with as_principal(_revealer(org_id, scope="OWN", subject_ref=data["subject_b"])):
            out_of_scope = await client.post(_path(data["contact_a_id"]))
        with as_principal(_revealer(uuid.uuid4())):
            foreign = await client.post(_path(data["contact_a_id"]))

        assert [r.status_code for r in (unknown, bad_path, out_of_scope, foreign)] == [
            404,
            422,
            403,
            404,
        ]
        assert redis.store == {}
        assert await _committed_reads(admin_session, org_id) == []

    async def test_a_token_cannot_burn_the_real_persons_budget_with_refused_requests(
        self, client, org_id, data, monkeypatch
    ) -> None:
        """The abuse the ordering closes: whoever holds the token sends a run of requests that
        were never going to reveal anything, and the person it belongs to is then told their
        budget is spent. Now the run costs them nothing, and their own reveal still works."""
        _tighten(monkeypatch, burst=3)
        with as_principal(_revealer(org_id)):
            for _ in range(25):
                assert (await client.post(_path(uuid.uuid4()))).status_code == 404
            revealed = await client.post(_path(data["contact_a_id"]))
        assert revealed.status_code == 200

    async def test_only_a_request_that_opens_a_contact_is_counted(
        self, client, org_id, data, monkeypatch
    ) -> None:
        _tighten(monkeypatch, burst=2)
        with as_principal(_revealer(org_id)):
            await client.post(_path(uuid.uuid4()))  # refused: free
            for _ in range(2):
                assert (await client.post(_path(data["contact_a_id"]))).status_code == 200
            over = await client.post(_path(data["contact_a_id"]))
        assert over.status_code == 429

    async def test_a_caller_who_may_not_reveal_spends_nothing(
        self, client, org_id, data, redis: FakeRedis
    ) -> None:
        """The budget follows the permission and the step-up, so an unauthorised caller neither
        spends anything nor reaches Redis at all."""
        no_permission = principal_for(org_id, [], amr=STEPPED_UP)
        no_step_up = principal_for(org_id, [f"{SENSITIVE}:ORG"])
        for principal in (no_permission, no_step_up):
            with as_principal(principal):
                response = await client.post(_path(data["contact_a_id"]))
            assert response.status_code == 403
        assert redis.store == {}

    async def test_the_budget_is_spent_after_the_lookup_and_before_the_decrypt(
        self, client, session, org_id, data, monkeypatch
    ) -> None:
        """The order is the property: cheap checks first (the contact exists and is in scope),
        then the budget, then — only then — the number is opened."""
        await _seal_a_number(session, org_id, data)
        order: list[str] = []
        real_get, real_budget, real_open = (
            services.selectors.get_contact,
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

        monkeypatch.setattr(services.selectors, "get_contact", _get)
        monkeypatch.setattr(services, "enforce_reveal_budget", _budget)
        monkeypatch.setattr(services, "open_sealed", _open)

        with as_principal(_revealer(org_id)):
            response = await client.post(_path(data["contact_a_id"]))

        assert response.status_code == 200
        assert order == ["lookup", "budget", "decrypt"]

    def test_the_guard_does_not_spend_the_budget(self) -> None:
        """The dependency that runs ahead of the route body only decides authorization. Were it to
        spend the budget again, a refused request would be counted before it is known to be one."""
        import inspect

        from app.api import routes

        assert "enforce_reveal_budget" not in inspect.getsource(routes._reveal_guard)

    async def test_a_redis_that_never_answers_is_the_same_503_and_is_not_waited_for(
        self, client, session, org_id, data, admin_session, monkeypatch, redis: FakeRedis
    ) -> None:
        """The budget is spent inside the request's database transaction, so a Redis that neither
        answers nor refuses must not hold the connection until some socket timeout: it is bounded,
        and over the bound it is an outage — nothing revealed, nothing recorded."""
        import asyncio

        from app import services

        redis.hang_on = 1
        monkeypatch.setattr(services, "_REVEAL_BUDGET_TIMEOUT_SECONDS", 0.05)

        with as_principal(_revealer(org_id)):
            response = await asyncio.wait_for(client.post(_path(data["contact_a_id"])), timeout=5)

        assert response.status_code == 503
        assert RAW not in response.text
        assert await _committed_reads(admin_session, org_id) == []

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
            response = await asyncio.wait_for(client.post(_path(data["contact_a_id"])), timeout=5)

        assert set(redis.store) <= set(redis.expiries), "a counter was left with no expiry"
        assert redis.round_trips == 1, "the budget check is one round trip"
        if hang_on == 1:
            assert response.status_code == 503
            assert RAW not in response.text
            assert await _committed_reads(admin_session, org_id) == []
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
            ok = await client.post(_path(data["contact_a_id"]))
            assert ok.status_code == 200
            assert redis.round_trips == 1
            assert len(redis.store) == 2  # both windows, spent together

            redis.hang_on = 2
            monkeypatch.setattr(services, "_REVEAL_BUDGET_TIMEOUT_SECONDS", 0.3)
            started = time.monotonic()
            slow = await asyncio.wait_for(client.post(_path(data["contact_a_id"])), timeout=5)
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
                assert (await client.post(_path(data["contact_a_id"]))).status_code == 200
            for _ in range(25):
                assert (await client.post(_path(data["contact_a_id"]))).status_code == 429

        by_prefix = {key.split(":")[1]: count for key, count in redis.store.items()}
        assert by_prefix == {"crm.reveal.burst": 3, "crm.reveal.daily": 3}

    async def test_a_request_refused_for_the_day_does_not_spend_the_burst(
        self, client, org_id, data, redis: FakeRedis, monkeypatch
    ) -> None:
        _tighten(monkeypatch, burst=1000, daily=2)
        with as_principal(_revealer(org_id)):
            for _ in range(2):
                await client.post(_path(data["contact_a_id"]))
            for _ in range(10):
                refused = await client.post(_path(data["contact_a_id"]))
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
            response = await client.post(_path(data["contact_a_id"]))

        assert response.status_code == 200
        assert redis.expiries[orphan] == 2 * 86_400
        assert redis.store[orphan] == 2

    async def test_the_keys_are_ids_only_and_the_prefix_is_the_one_lead_uses(
        self, client, org_id, data, redis: FakeRedis
    ) -> None:
        """One budget for both reveal routes: lead-service spends from
        `rl:crm.reveal.{burst,daily}:<org>:<user>:<window>` and so must this. A change of prefix
        here silently doubles what one person can read."""
        principal = _revealer(org_id)
        with as_principal(principal):
            await client.post(_path(data["contact_a_id"]))
        assert len(redis.store) == 2  # the burst window and the daily window
        prefixes = {key.split(":", 2)[1] for key in redis.store}
        assert prefixes == {"crm.reveal.burst", "crm.reveal.daily"}
        for key in redis.store:
            assert f":{org_id}:{principal.sub}:" in key
            assert str(data["contact_a_id"]) not in key


# --------------------------------------------------------------------- a Redis outage


class TestRedisOutage:
    """What an unreachable Redis does to this service, precisely: reveals stop (503, nothing
    opened, nothing audited), readiness reports it, and every other route carries on."""

    async def test_reveals_fail_closed_and_open_nothing(
        self, client, session, org_id, data, redis: FakeRedis, admin_session
    ) -> None:
        await _seal_a_number(session, org_id, data)
        redis.poisoned = True
        with as_principal(_revealer(org_id)):
            response = await client.post(_path(data["contact_a_id"]))

        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["status"] == 503
        assert RAW not in response.text
        assert RAW.removeprefix("+") not in response.text
        assert await _committed_reads(admin_session, org_id) == []

    async def test_no_client_at_all_is_the_same_outage(
        self, client, org_id, data, monkeypatch
    ) -> None:
        """A process whose lifespan never opened Redis (`get_redis` raises) is refused the same
        way — never waved through."""
        monkeypatch.setattr(cache, "_redis", None)
        with as_principal(_revealer(org_id)):
            response = await client.post(_path(data["contact_a_id"]))
        assert response.status_code == 503

    async def test_every_other_route_carries_on(
        self, client, org_id, data, redis: FakeRedis
    ) -> None:
        redis.poisoned = True
        reader = principal_for(org_id, ["crm.book.contact.read:ORG", "crm.book.company.read:ORG"])
        with as_principal(reader):
            contact = await client.get(f"/api/v1/contacts/{data['contact_a_id']}")
            companies = await client.get("/api/v1/customers")
        assert contact.status_code == 200
        assert companies.status_code == 200
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
    """One structured WARNING per refusal — never an audit row — naming who, where, which contact
    and why, and never a value."""

    async def _refused(self, client, principal: Principal, path: str):
        with capture_logs() as entries, as_principal(principal):
            response = await client.post(path)
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
            "contact_id",
            "request_id",
        }
        blob = json.dumps(line, default=str)
        assert RAW.removeprefix("+") not in blob

    async def test_a_caller_without_the_permission(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["crm.book.contact.read:ORG"], amr=STEPPED_UP)
        response, lines, _ = await self._refused(client, principal, _path(data["contact_a_id"]))

        assert response.status_code == 403
        (line,) = lines
        self._assert_line(line, org_id=org_id, principal=principal, reason="missing_permission")
        assert line["contact_id"] == str(data["contact_a_id"])
        assert line["request_id"] == response.headers["x-request-id"]

    async def test_a_caller_who_has_not_stepped_up(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [f"{SENSITIVE}:ORG"])
        response, lines, _ = await self._refused(client, principal, _path(data["contact_a_id"]))

        assert response.status_code == 403
        (line,) = lines
        self._assert_line(line, org_id=org_id, principal=principal, reason="step_up_required")
        assert line["contact_id"] == str(data["contact_a_id"])

    async def test_a_contact_outside_the_callers_scope(self, client, org_id, data) -> None:
        principal = _revealer(org_id, scope="OWN", subject_ref=data["subject_b"])
        response, lines, _ = await self._refused(client, principal, _path(data["contact_a_id"]))

        assert response.status_code == 403
        (line,) = lines
        self._assert_line(line, org_id=org_id, principal=principal, reason="out_of_scope")
        assert line["contact_id"] == str(data["contact_a_id"])

    async def test_a_contact_that_is_not_in_this_organization(self, client, org_id, data) -> None:
        principal = _revealer(uuid.uuid4())  # another tenant's caller, this tenant's contact
        response, lines, _ = await self._refused(client, principal, _path(data["contact_a_id"]))

        assert response.status_code == 404
        (line,) = lines
        self._assert_line(
            line,
            org_id=principal.organization_id,
            principal=principal,
            reason="contact_not_found",
        )
        assert line["contact_id"] == str(data["contact_a_id"])

    async def test_a_spent_budget(self, client, org_id, data, monkeypatch) -> None:
        _tighten(monkeypatch, burst=1)
        principal = _revealer(org_id)
        with as_principal(principal):
            await client.post(_path(data["contact_a_id"]))
        response, lines, _ = await self._refused(client, principal, _path(data["contact_a_id"]))

        assert response.status_code == 429
        (line,) = lines
        self._assert_line(line, org_id=org_id, principal=principal, reason="rate_limited_burst")

    async def test_a_path_that_is_not_a_uuid_logs_no_contact_id(self, client, org_id) -> None:
        """The path parameter is client text: it is logged only once it parses as a UUID."""
        principal = principal_for(org_id, [], amr=STEPPED_UP)
        response, lines, _ = await self._refused(
            client, principal, "/api/v1/contacts/<script>/phone:reveal"
        )

        assert response.status_code == 403
        (line,) = lines
        assert line["contact_id"] is None
        assert "<script>" not in json.dumps(line)

    async def test_a_permitted_reveal_logs_no_refusal(self, client, org_id, data) -> None:
        response, lines, _ = await self._refused(
            client, _revealer(org_id), _path(data["contact_a_id"])
        )
        assert response.status_code == 200
        assert lines == []

    async def test_a_refusal_writes_no_audit_row(self, client, org_id, data, admin_session) -> None:
        for principal in (
            principal_for(org_id, [], amr=STEPPED_UP),
            _revealer(org_id, scope="OWN", subject_ref=data["subject_b"]),
        ):
            response, lines, _ = await self._refused(client, principal, _path(data["contact_a_id"]))
            assert response.status_code == 403
            assert len(lines) == 1
        assert await _committed_reads(admin_session, org_id) == []
