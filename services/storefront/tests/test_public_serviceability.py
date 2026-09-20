"""The anonymous check: no token, no account, no session.

This is the whole point of slice 1, so nothing here is stubbed on the paths that matter. The
slug lookup runs on the **real** `opsgrids_storefront_directory` role through the real directory
engine — which is also the only thing that proves `migrations/env.py` actually granted it — and
everything after it runs tenant-bound on the real application role, under the real policies,
committing for real.

Three of these tests exist because of failures that are silent by nature:

* the store that resolves to nothing is a **404**, not a well-formed "we don't deliver";
* "never mapped" and "mapped and excluded" are distinguishable, in the response *and* in the
  demand counter, because the report is worth far more when a merchant can tell them apart;
* the module that answers a buyer contains no exception handling at all, asserted against its
  own syntax tree rather than against a comment.
"""

from __future__ import annotations

import ast
import datetime as dt
import pathlib
import uuid

import pytest
from sqlalchemy import select

import app.api.public as public_module
from app.models import ServiceabilityDemand
from tests.helpers import seed_store, seed_zone

pytestmark = pytest.mark.integration

PATH = "/api/v1/storefront/public/serviceability"


async def _demand(admin_session, org_id: uuid.UUID) -> list[ServiceabilityDemand]:
    """Read the counter rows a request committed on another connection.

    The rollback is not ceremony: this session holds an open transaction from its last statement,
    and under READ COMMITTED a new one is what sees the row the request just wrote.
    """
    await admin_session.rollback()
    return list(
        (
            await admin_session.execute(
                select(ServiceabilityDemand).where(ServiceabilityDemand.organization_id == org_id)
            )
        )
        .scalars()
        .all()
    )


class TestTheAnonymousContract:
    async def test_it_answers_with_no_token_at_all(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """**No `Authorization` header, no principal override, nothing.** A green unit suite for
        the selector proves nothing about this; the whole slice is that a stranger can ask."""
        await seed_store(admin_session, org_id, slug)
        await seed_zone(admin_session, org_id, postal_codes=("400050",))

        response = await client.post(PATH, json={"store": slug, "postal_code": "400050"})

        assert response.status_code == 200
        body = response.json()
        assert body["serviceable"] is True
        assert body["mode"] == "PROMISED"
        assert body["reason"] is None
        assert body["promise"] == {"kind": "DURATION", "minutes": 30, "date": None}

    async def test_the_postal_code_comes_back_normalised(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug)
        await seed_zone(admin_session, org_id, postal_codes=("400050",))

        response = await client.post(PATH, json={"store": slug, "postal_code": "400 050"})

        assert response.status_code == 200
        assert response.json()["postal_code"] == "400050"

    async def test_fees_come_back_marked_display_only(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """**These are never the numbers charged.** The pricing engine recomputes from its own
        state when the cart lands, and one of the two sources of truth stops being authoritative
        — this one (ADR-0010 §8). The flag is in the payload so the caveat is rendered from the
        response rather than from a constant somebody has to remember to remove."""
        await seed_store(admin_session, org_id, slug)
        await seed_zone(admin_session, org_id, postal_codes=("400050",))

        body = (await client.post(PATH, json={"store": slug, "postal_code": "400050"})).json()

        assert body["fees"] == {
            "currency": "INR",
            "delivery_fee_minor": 2900,
            "free_delivery_above_minor": 49900,
            "min_order_minor": 19900,
            "display_only": True,
        }

    async def test_an_unknown_field_on_the_body_is_refused(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """The anonymous body is the one input on this service that arrives from a stranger.
        Silently dropping what it does not recognise is how an undocumented field becomes a
        contract."""
        await seed_store(admin_session, org_id, slug)

        response = await client.post(
            PATH,
            json={"store": slug, "postal_code": "400050", "organization_id": str(uuid.uuid4())},
        )

        assert response.status_code == 422

    async def test_a_malformed_postal_code_is_422_and_not_an_answer(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """A typo must never teach a buyer that the shop does not reach them."""
        await seed_store(admin_session, org_id, slug)

        response = await client.post(PATH, json={"store": slug, "postal_code": "nope"})

        assert response.status_code == 422
        assert "serviceable" not in response.json()


class TestResolvingTheStore:
    async def test_an_unknown_slug_is_404_not_a_negative_answer(self, client) -> None:
        """Distinct in both status and log from "resolved, and not served" (ADR-0010 §4)."""
        response = await client.post(
            PATH, json={"store": f"t{uuid.uuid4().hex[:16]}", "postal_code": "400050"}
        )

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")
        assert "serviceable" not in response.json()

    @pytest.mark.parametrize("status", ["draft", "suspended"])
    async def test_an_unpublished_store_is_invisible(
        self, client, admin_session, org_id: uuid.UUID, slug: str, status: str
    ) -> None:
        """A merchant maps zones and uses the tester before any buyer can ask."""
        await seed_store(admin_session, org_id, slug, status=status)
        await seed_zone(admin_session, org_id, postal_codes=("400050",))

        response = await client.post(PATH, json={"store": slug, "postal_code": "400050"})

        assert response.status_code == 404

    async def test_no_demand_row_is_written_for_a_store_that_does_not_resolve(
        self, client, admin_session, org_id: uuid.UUID
    ) -> None:
        """Nothing tenant-scoped happens before a tenant exists, which is the point of resolving
        on a bypass reader that may only read."""
        await client.post(
            PATH, json={"store": f"t{uuid.uuid4().hex[:16]}", "postal_code": "400050"}
        )

        assert await _demand(admin_session, org_id) == []


class TestUnmappedIsNotTheSameAsRefused:
    """ "We have not got round to it" and "we decided not to" are different facts, and the demand
    report is worth far more when a merchant can tell them apart (ADR-0010 §3)."""

    async def test_a_postal_code_that_was_never_mapped_says_so(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug)
        await seed_zone(admin_session, org_id, postal_codes=("400050",))

        body = (await client.post(PATH, json={"store": slug, "postal_code": "422001"})).json()

        assert body["serviceable"] is False
        assert body["mode"] == "UNSERVED"
        assert body["reason"] == "no_mapping"
        assert body["fees"] is None and body["promise"] is None

    async def test_a_deliberately_excluded_postal_code_says_something_else(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug)
        await seed_zone(
            admin_session,
            org_id,
            code="EXCLUDED",
            mode="UNSERVED",
            promise_kind=None,
            promise_minutes=None,
            postal_codes=("194101",),
        )

        body = (await client.post(PATH, json={"store": slug, "postal_code": "194101"})).json()

        assert body["reason"] == "excluded"

    async def test_a_switched_off_zone_is_a_third_distinct_reason(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug)
        await seed_zone(admin_session, org_id, is_active=False, postal_codes=("400050",))

        body = (await client.post(PATH, json={"store": slug, "postal_code": "400050"})).json()

        assert body["reason"] == "zone_inactive"


class TestTheDemandCounter:
    async def test_a_check_increments_one_row_rather_than_appending_one(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """A row per check would be an unbounded **unauthenticated write** — a free way for any
        stranger to grow a merchant's table, throttled only by a per-IP limit a botnet does not
        respect (ADR-0010 §7)."""
        await seed_store(admin_session, org_id, slug)
        await seed_zone(admin_session, org_id, postal_codes=("400050",))

        for _ in range(3):
            await client.post(PATH, json={"store": slug, "postal_code": "422001"})

        rows = await _demand(admin_session, org_id)
        assert len(rows) == 1
        assert rows[0].checks == 3
        assert rows[0].outcome == "NO_MAPPING"

    async def test_the_counter_records_the_same_decision_the_buyer_was_given(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """Written in the same transaction as the read, so there is no "recorded served, returned
        unserved" drift to debug."""
        await seed_store(admin_session, org_id, slug)
        await seed_zone(admin_session, org_id, postal_codes=("400050",))

        await client.post(PATH, json={"store": slug, "postal_code": "400050"})
        await client.post(PATH, json={"store": slug, "postal_code": "422001"})

        rows = {row.postal_code: row.outcome for row in await _demand(admin_session, org_id)}
        assert rows == {"400050": "SERVED", "422001": "NO_MAPPING"}

    async def test_the_counter_is_keyed_on_the_stores_own_calendar_day(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """A merchant's daily totals break at their own midnight, not at UTC's."""
        await seed_store(admin_session, org_id, slug, timezone="Asia/Kolkata")
        await seed_zone(admin_session, org_id, postal_codes=("400050",))

        await client.post(PATH, json={"store": slug, "postal_code": "400050"})

        rows = await _demand(admin_session, org_id)
        expected = (
            dt.datetime.now(dt.UTC)
            .astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30)))
            .date()
        )
        assert rows[0].check_date == expected


class TestThePromisedDate:
    async def test_a_date_offset_zone_answers_with_a_resolved_date_not_prose(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """The client owns the wording; the server owns the date, and computes it from the
        store's timezone rather than the browser's."""
        await seed_store(admin_session, org_id, slug, timezone="Asia/Kolkata")
        await seed_zone(
            admin_session,
            org_id,
            code="UPCOUNTRY",
            promise_kind="DATE_OFFSET",
            promise_minutes=None,
            promise_offset_days=2,
            promise_cutoff_time=dt.time(23, 59),
            postal_codes=("422001",),
        )

        body = (await client.post(PATH, json={"store": slug, "postal_code": "422001"})).json()

        local_today = (
            dt.datetime.now(dt.UTC)
            .astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30)))
            .date()
        )
        assert body["promise"]["kind"] == "DATE_OFFSET"
        assert body["promise"]["minutes"] is None
        assert body["promise"]["date"] == (local_today + dt.timedelta(days=2)).isoformat()


class TestCrossTenantIsolationOnTheAnonymousPath:
    async def test_another_organizations_zones_are_invisible_even_with_the_same_postal_code(
        self, client, admin_session, org_id: uuid.UUID, other_org: uuid.UUID, slug: str
    ) -> None:
        """The bypass reader buys **discovery only**. Everything after it runs on the application
        role inside `session_scope`, so a stranger's request is policied exactly like a member's —
        and a postal code served by a *different* merchant is not served by this one."""
        await seed_store(admin_session, org_id, slug)
        await seed_zone(admin_session, other_org, postal_codes=("400050",))

        body = (await client.post(PATH, json={"store": slug, "postal_code": "400050"})).json()

        assert body["serviceable"] is False
        assert body["reason"] == "no_mapping"

    async def test_the_demand_row_lands_on_the_store_that_was_asked_about(
        self, client, admin_session, org_id: uuid.UUID, other_org: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug)

        await client.post(PATH, json={"store": slug, "postal_code": "400050"})

        assert len(await _demand(admin_session, org_id)) == 1
        assert await _demand(admin_session, other_org) == []


class TestUnknownIsNotNo:
    """ADR-0010 §13's rule, made structural rather than commented.

    A check that fails must render as "we couldn't check", never as "we don't deliver here". The
    server never returns an `UNSERVED` outcome it did not compute, and `UNKNOWN` exists only as a
    client state produced by a transport failure. These two tests are what keeps that true when
    somebody adds a `try` for a good reason at three in the morning.
    """

    def test_the_public_module_contains_no_exception_handling(self) -> None:
        source = pathlib.Path(public_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders = [
            type(node).__name__
            for node in ast.walk(tree)
            if isinstance(node, ast.Try | ast.ExceptHandler)
        ]
        assert offenders == [], (
            "app/api/public.py has grown exception handling. A `catch → not serviceable` is "
            "exactly the failure ADR-0010 §13 exists to prevent: a buyer in a served area is "
            "told the shop does not reach them, and leaves. Let it raise — the client renders "
            "the non-2xx as 'couldn't check'."
        )

    def test_the_public_module_never_names_an_unserved_outcome(self) -> None:
        """The only producer of an `UNSERVED` answer is `selectors.resolve_serviceability`. A
        literal here would mean something else had started deciding."""
        source = pathlib.Path(public_module.__file__).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        assert '"UNSERVED"' not in code and "'UNSERVED'" not in code

    async def test_a_failing_check_is_a_problem_not_an_answer(
        self, client, admin_session, org_id: uuid.UUID, slug: str, monkeypatch
    ) -> None:
        """With the database unreachable mid-request, the response is a 5xx problem+json and
        carries **no** `serviceable` field — there is no path that turns a failure into a no."""
        await seed_store(admin_session, org_id, slug)

        async def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("postgres went away")

        monkeypatch.setattr("app.api.public.services.check_serviceability_for_store", _boom)

        with pytest.raises(RuntimeError):
            await client.post(PATH, json={"store": slug, "postal_code": "400050"})
