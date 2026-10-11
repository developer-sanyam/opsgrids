"""Authorization, scope and validation — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth
more than proving one succeeds. The scope tests come in two halves — `ctx.apply` narrows a
*list*, `ctx.assert_can_touch` guards a single *object* — because filtering the list and
forgetting the detail is the named vulnerability. A rep who can list only their own deals
must also be refused another's by id.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.main import app
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from tests.helpers import ctx_for, principal_for

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("pipeline_mode")]


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# --------------------------------------------------------------------- list scope


class TestListScope:
    async def _deal_names(self, session: AsyncSession, ctx: ScopeContext) -> set[str]:
        rows = (await session.execute(selectors.deals_stmt(ctx))).scalars().all()
        return {d.name for d in rows}

    async def test_org_scope_sees_the_whole_board(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.read", Scope.ORG)
        assert await self._deal_names(session, ctx) == {
            "Fleet telematics rollout",
            "Omnichannel CRM",
        }

    async def test_own_scope_sees_only_deals_you_run(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.read", Scope.OWN, subject_ref=data["subject_a"])
        assert await self._deal_names(session, ctx) == {"Fleet telematics rollout"}

    async def test_own_scope_with_no_subject_sees_nothing(self, session, org_id, data) -> None:
        """OWN with nothing to own must match nothing, never everything."""
        ctx = ctx_for(org_id, "crm.pipeline.deal.read", Scope.OWN, subject_ref=None)
        assert await self._deal_names(session, ctx) == set()

    async def test_a_search_cannot_widen_scope(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.read", Scope.OWN, subject_ref=data["subject_a"])
        stmt = selectors.deals_stmt(ctx, q="omnichannel")  # matches only subject_b's deal
        assert (await session.execute(stmt)).all() == []

    async def test_an_owner_filter_cannot_widen_scope(self, session, org_id, data) -> None:
        """`owner_id=` is a filter within scope, never a widening: an OWN-scoped rep asking
        for another rep's deals gets nothing, not theirs."""
        ctx = ctx_for(org_id, "crm.pipeline.deal.read", Scope.OWN, subject_ref=data["subject_a"])
        stmt = selectors.deals_stmt(ctx, owner_id=data["subject_b"])
        assert (await session.execute(stmt)).all() == []

    async def test_own_scope_narrows_the_timeline_too(self, session, org_id, data) -> None:
        ctx = ctx_for(
            org_id, "crm.pipeline.activity.read", Scope.OWN, subject_ref=data["subject_b"]
        )
        rows = (await session.execute(selectors.activities_stmt(ctx))).scalars().all()
        assert {a.subject for a in rows} == {"Kickoff - Harbor & Co go-live"}


# --------------------------------------------------------------------- object-level


class TestObjectLevelChecks:
    async def test_scope_does_not_leak_through_the_detail_route(self, client, org_id, data) -> None:
        """A caller with OWN scope is refused another rep's deal by id, but not their own."""
        principal = principal_for(
            org_id, ["crm.pipeline.deal.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            refused = await client.get(f"/api/v1/deals/{data['deal_b_id']}")
            allowed = await client.get(f"/api/v1/deals/{data['deal_a_id']}")
        assert refused.status_code == 403
        assert allowed.status_code == 200
        assert allowed.json()["name"] == "Fleet telematics rollout"


# --------------------------------------------------------------------- endpoint authz


def _read_endpoints(data: dict) -> list[str]:
    return [
        "/api/v1/deals",
        "/api/v1/deals/summary",
        "/api/v1/deals/activities",
        f"/api/v1/deals/{data['deal_a_id']}",
    ]


class TestEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id, data) -> None:
        for path in _read_endpoints(data):
            response = await client.get(path)
            assert response.status_code == 401, path

    async def test_missing_the_permission_is_forbidden(self, client, org_id, data) -> None:
        """Entitled to CRM, but holding no CRM permission at all."""
        principal = principal_for(org_id, [], subject_ref=data["subject_a"])
        with as_principal(principal):
            for path in _read_endpoints(data):
                response = await client.get(path)
                assert response.status_code == 403, path
                assert response.headers["content-type"].startswith("application/problem+json")

    async def test_the_deal_permission_does_not_open_the_timeline(
        self, client, org_id, data
    ) -> None:
        """Permissions are per resource: deal.read alone reads no activities (and the
        other way around) — a coarse 'has any crm permission' check would be a leak."""
        deal_only = principal_for(
            org_id, ["crm.pipeline.deal.read:ORG"], subject_ref=data["subject_a"]
        )
        activity_only = principal_for(
            org_id, ["crm.pipeline.activity.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(deal_only):
            timeline = await client.get("/api/v1/deals/activities")
        with as_principal(activity_only):
            deals = await client.get("/api/v1/deals")
            summary = await client.get("/api/v1/deals/summary")
        assert timeline.status_code == 403
        assert deals.status_code == 403
        assert summary.status_code == 403

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["crm.pipeline.deal.read:ORG", "crm.pipeline.activity.read:ORG"],
            subject_ref=data["subject_a"],
            entitlements=[],
        )
        with as_principal(principal):
            deals = await client.get("/api/v1/deals")
            timeline = await client.get("/api/v1/deals/activities")
        assert deals.status_code == 402
        assert timeline.status_code == 402

    async def test_an_own_reader_sees_only_their_deals_over_http(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.deal.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/deals")
        assert response.status_code == 200
        names = {item["name"] for item in response.json()["items"]}
        assert names == {"Fleet telematics rollout"}

    async def test_the_summary_is_computed_over_the_callers_scope(
        self, client, org_id, data
    ) -> None:
        """An OWN-scoped rep's summary is their own book of business, not the org's."""
        principal = principal_for(
            org_id, ["crm.pipeline.deal.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/deals/summary")
        assert response.status_code == 200
        body = response.json()
        assert body["open_deals"] == 1
        assert body["pipeline_value_minor"] == 32_000_000 * 100
        assert body["win_rate_pct"] == 0  # subject_a has no decided deals

    async def test_owner_equals_me_narrows_an_org_readers_summary_to_their_own_book(
        self, client, org_id, data
    ) -> None:
        """`owner=me`, resolved from the token, gives an ORG-scoped reader just their own
        pipeline — the same numbers the OWN-scoped test above sees, on a wider grant."""
        principal = principal_for(
            org_id, ["crm.pipeline.deal.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/deals/summary?owner=me")
        assert response.status_code == 200
        body = response.json()
        assert body["open_deals"] == 1
        assert body["pipeline_value_minor"] == 32_000_000 * 100

    async def test_owner_id_narrows_an_org_readers_summary_to_the_named_rep(
        self, client, org_id, data
    ) -> None:
        """`owner_id` names a specific rep, the way the list endpoint's own filter does — an
        ORG-scoped manager can pull one rep's numbers without seeing the whole book conflated."""
        principal = principal_for(
            org_id, ["crm.pipeline.deal.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get(f"/api/v1/deals/summary?owner_id={data['subject_b']}")
        assert response.status_code == 200
        body = response.json()
        assert body["open_deals"] == 0  # subject_b's only deal is closed_won
        assert body["won_value_minor"] == 21_000_000 * 100

    async def test_owner_id_cannot_widen_an_own_scoped_readers_summary(
        self, client, org_id, data
    ) -> None:
        """The negative case: an OWN-scoped rep naming a colleague's id must get nothing back —
        neither the colleague's numbers nor, by falling back silently, their own whole-org view.
        `ctx.apply` and the `owner_id` filter both hold at once."""
        principal = principal_for(
            org_id, ["crm.pipeline.deal.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get(f"/api/v1/deals/summary?owner_id={data['subject_b']}")
        assert response.status_code == 200
        body = response.json()
        assert body["open_deals"] == 0
        assert body["pipeline_value_minor"] == 0
        assert body["won_value_minor"] == 0

    async def test_owner_equals_me_with_no_subject_ref_is_the_all_zero_rollup(
        self, client, org_id, data
    ) -> None:
        """A caller with no `subject_ref` asking for `owner=me` has nothing to resolve "me" to —
        the all-zero rollup, never the unfiltered book."""
        principal = principal_for(org_id, ["crm.pipeline.deal.read:ORG"], subject_ref=None)
        with as_principal(principal):
            response = await client.get("/api/v1/deals/summary?owner=me")
        assert response.status_code == 200
        body = response.json()
        assert body["open_deals"] == 0
        assert body["pipeline_value_minor"] == 0
        assert body["trend"] is None

    async def test_the_wire_shape_matches_the_spa_contract(self, client, org_id, data) -> None:
        """The list row carries exactly the SPA's `Deal` fields — the flag-flip check."""
        principal = principal_for(
            org_id, ["crm.pipeline.deal.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/deals")
        row = response.json()["items"][0]
        assert set(row) == {
            "id",
            "name",
            "company_id",
            "primary_contact_id",
            "value_minor",
            "currency",
            "stage",
            "owner_id",
            "probability",
            "close_date",
            "created_at",
            "last_activity_at",
            "source",
        }
        assert isinstance(row["value_minor"], int)

    async def test_the_timeline_filters_by_deal_and_type(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.activity.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            by_deal = await client.get(f"/api/v1/deals/activities?deal_id={data['deal_a_id']}")
            by_type = await client.get("/api/v1/deals/activities?type=meeting")
        assert [a["subject"] for a in by_deal.json()["items"]] == [
            "Discovery call on telematics scope"
        ]
        assert [a["type"] for a in by_type.json()["items"]] == ["meeting"]


# --------------------------------------------------------------------- validation


class TestValidation:
    async def test_a_duplicate_deal_name_is_refused(self, session, org_id, data) -> None:
        from datetime import date

        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        with pytest.raises(ConflictProblem, match="already exists"):
            async with session.begin():
                await services.create_deal(
                    session,
                    ctx,
                    name="Fleet telematics rollout",
                    company_id=data["company_a"],
                    owner_id=data["subject_a"],
                    value_minor=1,
                    close_date=date(2026, 12, 1),
                )

    async def test_an_unknown_stage_is_refused(self, session, org_id, data) -> None:
        from datetime import date

        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        with pytest.raises(ValidationProblem, match="not a stage of this pipeline") as refused:
            async with session.begin():
                await services.create_deal(
                    session,
                    ctx,
                    name="Bad stage",
                    company_id=data["company_a"],
                    owner_id=data["subject_a"],
                    value_minor=1,
                    close_date=date(2026, 12, 1),
                    stage="galaxy_brain",
                )
        assert refused.value.errors is not None
        assert refused.value.errors[0]["code"] == "unknown_stage"

    async def test_logging_against_an_unknown_deal_is_refused(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.activity.write", Scope.ORG)
        with pytest.raises(NotFoundProblem, match="deal"):
            async with session.begin():
                await services.log_activity(
                    session,
                    ctx,
                    activity_type="note",
                    subject="Ghost",
                    owner_id=data["subject_a"],
                    deal_id=uuid.uuid4(),
                )

    async def test_creating_stages_an_audit_row_and_an_outbox_event(
        self, session, org_id, data
    ) -> None:
        """The change, its audit row and its event commit together — nothing publishes inline."""
        from datetime import date

        from sqlalchemy import func, select

        from app.models import AuditLog, OutboxEvent

        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        async with session.begin():
            deal = await services.create_deal(
                session,
                ctx,
                name="Audited deal",
                company_id=data["company_a"],
                owner_id=data["subject_a"],
                value_minor=5_000_00,
                close_date=date(2026, 12, 1),
            )
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "deal.created", AuditLog.entity_id == deal.id)
            )
        ).scalar_one()
        events = (
            (await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == deal.id)))
            .scalars()
            .all()
        )
        assert audits == 1
        assert len(events) == 1
        assert events[0].event_type == "crm.pipeline.deal.created"
        assert events[0].published_at is None
