"""The customer Subscriptions page's plan reads and change-plan preview (ADR-0033).

Real PostgreSQL, the real customer routes behind a forged principal, a fake catalog. What is
pinned:

* **Who.** The preview and the picker take the change-plan write's own permission
  (`core.product.activation.write` at ORG); the per-row plan line takes the read permission. Each
  refuses a caller without it, a read-only caller where it writes, and an OWN-scoped grant.
* **Whose.** Another organization's subscription is a 404 on every new route and is never in the
  plan line; another organization's negotiated version is never offered and never priced.
* **When it lands is the server's answer, and the write agrees with it.** A table of states and
  directions: a trial moves now and free; a paid upgrade moves now with a prorated charge; a paid
  downgrade waits for the period end; an equal-price change moves now; a trial whose conversion is
  already claimed is paid time; re-picking the plan in force withdraws a waiting change.
* **Only what is on offer.** A superseded or retired-plan version is refused (`not_offerable`) by
  the preview *and* the write — the picker is not the only guard.
* **No list price reaches a tenant.** An amount appears only for this organization's own
  negotiated version, and only to a caller who may read its invoices.
* **The preview writes nothing**, and a stale confirm is a 409 `state_changed`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, services
from app.main import app
from app.models import (
    PERIOD_KIND_PRORATION,
    PERIOD_PENDING,
    STATE_ACTIVE,
    STATE_PAST_DUE,
    STATE_SUSPENDED,
    AuditLog,
    BillingPeriod,
    OutboxEvent,
    Subscription,
    SubscriptionTransition,
)
from app.schemas import CatalogPlanVersion, SubscribeIn
from platform_core.auth import Scope, ScopeContext, get_principal
from tests.helpers import MANAGE, READ, FakeCatalog, customer_principal, make_bundle, make_version

pytestmark = pytest.mark.integration

INVOICES = "core.billing.invoice.read"
#: The org owner as the platform seeds them: change plans, read subscriptions, read invoices.
OWNER = [f"{MANAGE}:ORG", f"{READ}:ORG", f"{INVOICES}:ORG"]
#: ORG_ADMIN: sees which products are active, cannot change them, cannot read invoices.
ADMIN = [f"{READ}:ORG"]


def _as(org_id: uuid.UUID, perms: list[str] | None = None) -> None:
    app.dependency_overrides[get_principal] = lambda: customer_principal(
        org_id, perms=OWNER if perms is None else perms
    )


def _ctx(org_id: uuid.UUID) -> ScopeContext:
    return ScopeContext(principal=customer_principal(org_id), scope=Scope.ORG, permission=MANAGE)


async def _subscribe(
    session: AsyncSession,
    catalog: FakeCatalog,
    org_id: uuid.UUID,
    version: CatalogPlanVersion,
    *,
    trial: bool = False,
) -> uuid.UUID:
    catalog.add(version)
    async with session.begin():
        sub = await services.subscribe(
            session,
            _ctx(org_id),
            catalog,
            SubscribeIn(
                product_key=version.product_key or "hrms",
                plan_version_id=version.id,
                trial_days=14 if trial else None,
            ),
        )
        return sub.id


async def _read(session: AsyncSession, subscription_id: uuid.UUID) -> Subscription:
    session.expunge_all()
    async with session.begin():
        return await services.require_subscription(session, subscription_id)


async def _force(
    admin: AsyncSession, session: AsyncSession, subscription_id: uuid.UUID, **columns: Any
) -> None:
    """Move a subscription's columns directly (BYPASSRLS) — states the routes do not reach. The
    test's own session is shared with the routes, so its cached row is dropped: a real request
    starts from a fresh session."""
    await admin.execute(
        update(Subscription).where(Subscription.id == subscription_id).values(**columns)
    )
    await admin.commit()
    session.expunge_all()


async def _counts(admin: AsyncSession, org_id: uuid.UUID) -> tuple[int, ...]:
    admin.expunge_all()
    out = []
    for model in (SubscriptionTransition, AuditLog, OutboxEvent, BillingPeriod):
        out.append(
            int(
                (
                    await admin.execute(
                        select(func.count())
                        .select_from(model)
                        .where(model.organization_id == org_id)
                    )
                ).scalar_one()
            )
        )
    return tuple(out)


async def _preview(client: AsyncClient, sub_id: uuid.UUID, target: uuid.UUID) -> Response:
    return await client.post(
        f"/api/v1/subscriptions/{sub_id}/change-plan/preview",
        json={"plan_version_id": str(target)},
    )


async def _change(
    client: AsyncClient,
    sub_id: uuid.UUID,
    target: uuid.UUID,
    expected: dict[str, Any] | None = None,
) -> Response:
    body: dict[str, Any] = {"plan_version_id": str(target)}
    if expected is not None:
        body["expected"] = expected
    return await client.post(f"/api/v1/subscriptions/{sub_id}/change-plan", json=body)


def _code(response: Response) -> str | None:
    errors = response.json().get("errors") or []
    return errors[0].get("code") if errors else None


def _stranger(other_org: uuid.UUID) -> Subscription:
    return Subscription(
        organization_id=other_org,
        product_key="hrms",
        plan_version_id=uuid.uuid4(),
        state=STATE_ACTIVE,
        currency="USD",
    )


# ------------------------------------------------------------------------------ who may ask


class TestAuthorization:
    @pytest.mark.parametrize(
        "perms",
        [[], [f"{READ}:ORG"], [f"{MANAGE}:OWN", f"{READ}:ORG"], [f"{INVOICES}:ORG"]],
        ids=["nothing", "read-only", "own-scoped-write", "invoices-only"],
    )
    async def test_the_preview_and_the_picker_need_the_change_plan_permission_at_org(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        perms: list[str],
    ) -> None:
        sub_id = await _subscribe(session, catalog, org_id, make_version(amount_minor=1000))
        target = catalog.add(make_version(amount_minor=2000))
        before = await _counts(admin_session, org_id)
        _as(org_id, perms)
        preview = await _preview(client, sub_id, target.id)
        options = await client.get(f"/api/v1/subscriptions/{sub_id}/plan-options")
        assert (preview.status_code, options.status_code) == (403, 403)
        assert catalog.offerable_calls == 0, "a refused caller must not reach catalog"
        assert await _counts(admin_session, org_id) == before

    @pytest.mark.parametrize("perms", [[], [f"{MANAGE}:ORG"], [f"{READ}:OWN"]])
    async def test_the_plan_line_needs_the_read_permission_at_org(
        self, client: AsyncClient, org_id: uuid.UUID, perms: list[str]
    ) -> None:
        _as(org_id, perms)
        response = await client.get("/api/v1/subscriptions/plan-summaries")
        assert response.status_code == 403

    async def test_a_read_only_member_sees_the_plan_line_without_amounts(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        negotiated = make_version(amount_minor=424242, restricted_to_org_id=org_id)
        await _subscribe(session, catalog, org_id, negotiated)
        _as(org_id, ADMIN)
        response = await client.get("/api/v1/subscriptions/plan-summaries")
        assert response.status_code == 200, response.text
        (row,) = response.json()
        assert row["current"]["plan_name"] == "Hrms plan"
        assert row["current"]["amount_visible"] is False
        assert "424242" not in response.text


# ------------------------------------------------------------------------------ whose


class TestCrossTenant:
    async def test_another_organizations_subscription_is_a_404_on_every_new_route(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        stranger = _stranger(other_org)
        admin_session.add(stranger)
        await admin_session.commit()
        target = catalog.add(make_version(amount_minor=2000))
        before = await _counts(admin_session, other_org)
        _as(org_id)
        preview = await _preview(client, stranger.id, target.id)
        options = await client.get(f"/api/v1/subscriptions/{stranger.id}/plan-options")
        change = await _change(client, stranger.id, target.id)
        assert (preview.status_code, options.status_code, change.status_code) == (404, 404, 404)
        assert catalog.offerable_calls == 0
        assert await _counts(admin_session, other_org) == before

    async def test_the_plan_line_lists_only_this_organizations_subscriptions(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        mine = await _subscribe(session, catalog, org_id, make_version(amount_minor=1000))
        stranger = _stranger(other_org)
        admin_session.add(stranger)
        await admin_session.commit()
        _as(org_id)
        response = await client.get("/api/v1/subscriptions/plan-summaries")
        assert [row["subscription_id"] for row in response.json()] == [str(mine)]
        assert str(stranger.id) not in response.text

    async def test_another_organizations_negotiated_version_is_neither_offered_nor_pinnable(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        sub_id = await _subscribe(session, catalog, org_id, make_version(amount_minor=1000))
        theirs = catalog.add(make_version(amount_minor=777001, restricted_to_org_id=other_org))
        _as(org_id)
        options = await client.get(f"/api/v1/subscriptions/{sub_id}/plan-options")
        assert str(theirs.id) not in options.text and "777001" not in options.text
        preview = await _preview(client, sub_id, theirs.id)
        assert preview.status_code == 404 and _code(preview) == domain.CODE_NOT_FOUND
        assert "777001" not in preview.text


# ------------------------------------------------------------------------------ when it lands


async def _claim_conversion(admin: AsyncSession, org_id: uuid.UUID, sub: Subscription) -> datetime:
    """A trial whose conversion the billing run has claimed: its time is paid from the claim."""
    assert sub.trial_ends_at is not None
    end = sub.trial_ends_at + timedelta(days=30)
    admin.add(
        BillingPeriod(
            organization_id=org_id,
            subscription_id=sub.id,
            kind=domain.KIND_TRIAL_CONVERSION,
            period_start=sub.trial_ends_at,
            period_end=end,
            plan_version_id=sub.plan_version_id,
            currency=sub.currency,
            items=[],
            state=PERIOD_PENDING,
            attempts=1,
        )
    )
    await admin.commit()
    return end


class TestTimingMatrix:
    @pytest.mark.parametrize(
        ("trial", "claimed", "target_minor", "expect"),
        [
            # A trial with nothing claimed moves now and free, whichever way.
            (True, False, 2000, ("upgrade", "now", False, False)),
            (True, False, 500, ("downgrade", "now", False, False)),
            # A paying subscription: an upgrade moves now with a prorated charge; a downgrade
            # waits for the period end with no credit; an equal price moves now, no proration.
            (False, False, 2000, ("upgrade", "now", True, True)),
            (False, False, 500, ("downgrade", "next_period", True, False)),
            (False, False, 1000, ("equal", "now", True, False)),
            # A trial whose conversion is already claimed is paid time at the claimed pin.
            (True, True, 2000, ("upgrade", "now", True, True)),
            (True, True, 500, ("downgrade", "next_period", True, False)),
        ],
        ids=[
            "trial-up",
            "trial-down",
            "paid-up",
            "paid-down",
            "paid-equal",
            "claimed-trial-up",
            "claimed-trial-down",
        ],
    )
    async def test_the_preview_says_what_the_write_then_does(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        trial: bool,
        claimed: bool,
        target_minor: int,
        expect: tuple[str, str, bool, bool],
    ) -> None:
        direction, effective, paid, prorated = expect
        sub_id = await _subscribe(
            session, catalog, org_id, make_version(amount_minor=1000), trial=trial
        )
        sub = await _read(session, sub_id)
        boundary = sub.current_period_end
        if claimed:
            boundary = await _claim_conversion(admin_session, org_id, sub)
        target = catalog.add(make_version(amount_minor=target_minor))
        _as(org_id)

        before = await _counts(admin_session, org_id)
        response = await _preview(client, sub_id, target.id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert await _counts(admin_session, org_id) == before, "the preview must write nothing"
        assert body["refusal_code"] is None, body
        assert (body["action"], body["direction"], body["effective"]) == (
            "switch",
            direction,
            effective,
        )
        assert (body["paid"], body["prorated_charge_now"]) == (paid, prorated)
        if effective == "next_period":
            assert boundary is not None
            assert datetime.fromisoformat(body["effective_at"]) == boundary
        else:
            assert body["effective_at"] is None

        changed = await _change(client, sub_id, target.id, body["expected"])
        assert changed.status_code == 200, changed.text
        after = await _read(session, sub_id)
        if effective == "now":
            assert after.plan_version_id == target.id and after.pending_plan_version_id is None
        else:
            assert after.plan_version_id == sub.plan_version_id
            assert after.pending_plan_version_id == target.id
        admin_session.expunge_all()
        prorations = (
            await admin_session.execute(
                select(func.count())
                .select_from(BillingPeriod)
                .where(
                    BillingPeriod.subscription_id == sub_id,
                    BillingPeriod.kind == PERIOD_KIND_PRORATION,
                    BillingPeriod.state == PERIOD_PENDING,
                )
            )
        ).scalar_one()
        assert bool(prorations) is prorated, "a prorated charge exactly when the preview said so"

    async def test_picking_the_plan_in_force_withdraws_a_waiting_change(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """'Keep current plan': the customer write supports it by re-pinning the plan in force."""
        current = make_version(amount_minor=1000)
        sub_id = await _subscribe(session, catalog, org_id, current)
        cheaper = catalog.add(make_version(amount_minor=500))
        _as(org_id)
        scheduled = await _change(client, sub_id, cheaper.id)
        assert scheduled.json()["pending_plan_version_id"] == str(cheaper.id)

        preview = (await _preview(client, sub_id, current.id)).json()
        assert (preview["action"], preview["effective"]) == ("withdraw_scheduled", "now")
        assert preview["would_clear_pending"] is True and preview["direction"] is None
        kept = await _change(client, sub_id, current.id, preview["expected"])
        assert kept.status_code == 200, kept.text
        after = await _read(session, sub_id)
        assert after.plan_version_id == current.id and after.pending_plan_version_id is None
        actions = [
            a.action
            for a in (
                await admin_session.execute(
                    select(AuditLog).where(AuditLog.entity_id == sub_id).order_by(AuditLog.at)
                )
            ).scalars()
        ]
        assert actions[-1] == "commerce.subscription.downgrade_withdrawn"

    async def test_a_plan_in_force_that_is_no_longer_offered_can_still_be_kept(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        plan = uuid.uuid4()
        old = make_version(amount_minor=1000, plan_id=plan, version_no=1)
        sub_id = await _subscribe(session, catalog, org_id, old)
        catalog.add(make_version(amount_minor=1100, plan_id=plan, version_no=2))
        cheaper = catalog.add(make_version(amount_minor=500))
        _as(org_id)
        await _change(client, sub_id, cheaper.id)
        kept = await _change(client, sub_id, old.id)
        assert kept.status_code == 200, kept.text
        assert (await _read(session, sub_id)).pending_plan_version_id is None


# ------------------------------------------------------------------------------ refusals


class TestRefusals:
    @pytest.mark.parametrize(
        ("target_kwargs", "code"),
        [
            ({"currency": "EUR"}, domain.CODE_CURRENCY),
            ({"billing_interval": "year"}, domain.CODE_CADENCE),
            ({"state": "draft"}, domain.CODE_NOT_PUBLISHED),
        ],
    )
    async def test_the_preview_answers_with_the_code_the_write_refuses_with(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        target_kwargs: dict[str, Any],
        code: str,
    ) -> None:
        sub_id = await _subscribe(session, catalog, org_id, make_version(amount_minor=1000))
        target = catalog.add(make_version(amount_minor=2000, **target_kwargs))
        _as(org_id)
        preview = await _preview(client, sub_id, target.id)
        assert preview.status_code == 200 and preview.json()["refusal_code"] == code
        before = await _counts(admin_session, org_id)
        write = await _change(client, sub_id, target.id)
        assert write.status_code == 422 and _code(write) == code
        assert await _counts(admin_session, org_id) == before

    @pytest.mark.parametrize("state", [STATE_PAST_DUE, STATE_SUSPENDED])
    async def test_a_subscription_that_owes_money_is_told_state_not_try_again(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        state: str,
    ) -> None:
        sub_id = await _subscribe(session, catalog, org_id, make_version(amount_minor=1000))
        await _force(
            admin_session,
            session,
            sub_id,
            state=state,
            current_period_end=datetime.now(UTC) - timedelta(days=2),
        )
        target = catalog.add(make_version(amount_minor=2000))
        _as(org_id)
        assert (await _preview(client, sub_id, target.id)).json()["refusal_code"] == (
            domain.CODE_STATE
        )
        options = (await client.get(f"/api/v1/subscriptions/{sub_id}/plan-options")).json()
        assert options["change_blocked_code"] == domain.CODE_STATE

    async def test_a_superseded_version_is_refused_by_the_write_not_only_hidden(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """Publishing v2 does not grandfather v1, so v1 is still published and pinnable by id. The
        picker does not offer it — and a direct call naming it is refused just the same."""
        sub_id = await _subscribe(session, catalog, org_id, make_version(amount_minor=2000))
        plan = uuid.uuid4()
        v1 = catalog.add(make_version(amount_minor=900, plan_id=plan, version_no=1))
        catalog.add(make_version(amount_minor=2500, plan_id=plan, version_no=2))
        _as(org_id)
        preview = (await _preview(client, sub_id, v1.id)).json()
        assert preview["refusal_code"] == domain.CODE_NOT_OFFERABLE
        before = await _counts(admin_session, org_id)
        write = await _change(client, sub_id, v1.id)
        assert write.status_code == 422 and _code(write) == domain.CODE_NOT_OFFERABLE
        assert await _counts(admin_session, org_id) == before
        assert (await _read(session, sub_id)).pending_plan_version_id is None

    async def test_a_retired_plans_version_is_not_offerable(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id = await _subscribe(session, catalog, org_id, make_version(amount_minor=1000))
        retired = catalog.add(make_version(amount_minor=2000))
        catalog.retired_plans.add(retired.plan_id)
        _as(org_id)
        assert (await _preview(client, sub_id, retired.id)).json()["refusal_code"] == (
            domain.CODE_NOT_OFFERABLE
        )
        assert (await _change(client, sub_id, retired.id)).status_code == 422

    async def test_billing_catching_up_is_an_answer_and_a_409(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id = await _subscribe(session, catalog, org_id, make_version(amount_minor=1000))
        await _force(
            admin_session, session, sub_id, current_period_end=datetime.now(UTC) - timedelta(days=1)
        )
        target = catalog.add(make_version(amount_minor=2000))
        _as(org_id)
        preview = (await _preview(client, sub_id, target.id)).json()
        assert preview["refusal_code"] == domain.CODE_BILLING_CATCHING_UP
        write = await _change(client, sub_id, target.id)
        assert write.status_code == 409 and _code(write) == domain.CODE_BILLING_CATCHING_UP

    async def test_a_bundle_subscription_is_answered_bundle_and_the_write_is_403(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id = await _subscribe(session, catalog, org_id, make_version(amount_minor=1000))
        await _force(admin_session, session, sub_id, entitled_products_snapshot=["hrms", "crm"])
        target = catalog.add(make_version(amount_minor=2000))
        _as(org_id)
        assert (await _preview(client, sub_id, target.id)).json()["refusal_code"] == (
            domain.CODE_BUNDLE
        )
        options = (await client.get(f"/api/v1/subscriptions/{sub_id}/plan-options")).json()
        assert options["change_blocked_code"] == domain.CODE_BUNDLE
        assert (await _change(client, sub_id, target.id)).status_code == 403

    async def test_a_bundle_rung_is_blocked_in_the_picker_and_answered_bundle(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id = await _subscribe(
            session, catalog, org_id, make_version(product_key="suite", amount_minor=1000)
        )
        rung = catalog.add(make_bundle(["hrms", "crm"], tier="duo", amount_minor=2000))
        _as(org_id)
        options = (await client.get(f"/api/v1/subscriptions/{sub_id}/plan-options")).json()
        (row,) = [o for o in options["options"] if o["plan_version_id"] == str(rung.id)]
        assert row["blocked_code"] == "bundle"
        assert (await _preview(client, sub_id, rung.id)).json()["refusal_code"] == "bundle"

    async def test_a_stale_confirm_is_a_409_and_changes_nothing(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """Previewed on a trial (free, now); the trial converted before the confirm. Applying
        blind would now be a paid change — the guard sends the customer back to look again."""
        sub_id = await _subscribe(
            session, catalog, org_id, make_version(amount_minor=1000), trial=True
        )
        target = catalog.add(make_version(amount_minor=2000))
        _as(org_id)
        expected = (await _preview(client, sub_id, target.id)).json()["expected"]
        await _force(admin_session, session, sub_id, state=STATE_ACTIVE)
        before = await _counts(admin_session, org_id)
        write = await _change(client, sub_id, target.id, expected)
        assert write.status_code == 409 and _code(write) == domain.CODE_STATE_CHANGED
        assert await _counts(admin_session, org_id) == before
        assert (await _read(session, sub_id)).plan_version_id != target.id

    async def test_an_unreachable_catalog_changes_nothing(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id = await _subscribe(session, catalog, org_id, make_version(amount_minor=1000))
        target = catalog.add(make_version(amount_minor=2000))
        catalog.offerable_down = True
        _as(org_id)
        before = await _counts(admin_session, org_id)
        preview = await _preview(client, sub_id, target.id)
        write = await _change(client, sub_id, target.id)
        assert (preview.status_code, write.status_code) == (503, 503)
        assert _code(write) == "catalog_unavailable"
        assert await _counts(admin_session, org_id) == before


# ------------------------------------------------------------------------------ the picker


class TestPlanOptions:
    async def test_each_offered_version_once_newest_only_the_current_plan_first_and_marked(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        """What was live on 2026-10-06: 'Revenue Growth' listed twice beside 'Revenue', no marker.
        Now: one row per offered version, the superseded one gone, the plan in force first."""
        growth = uuid.uuid4()
        old = make_version(amount_minor=1000, plan_id=growth, plan_name="Growth", version_no=1)
        sub_id = await _subscribe(session, catalog, org_id, old)
        newer = catalog.add(
            make_version(amount_minor=1200, plan_id=growth, plan_name="Growth", version_no=2)
        )
        negotiated = catalog.add(
            make_version(
                amount_minor=900,
                plan_id=growth,
                plan_name="Growth",
                version_no=3,
                restricted_to_org_id=org_id,
            )
        )
        theirs = catalog.add(
            make_version(
                amount_minor=800,
                plan_id=growth,
                plan_name="Growth",
                version_no=4,
                restricted_to_org_id=other_org,
            )
        )
        basic = catalog.add(make_version(amount_minor=500, plan_name="Basic"))
        yearly = catalog.add(make_version(amount_minor=9000, billing_interval="year"))
        euro = catalog.add(make_version(amount_minor=900, currency="EUR"))
        _as(org_id)
        body = (await client.get(f"/api/v1/subscriptions/{sub_id}/plan-options")).json()
        ids = [o["plan_version_id"] for o in body["options"]]
        assert len(ids) == len(set(ids)), "a version is offered once"
        assert ids[0] == str(old.id)
        first = body["options"][0]
        assert first["is_current"] is True and first["blocked_code"] == "current"
        assert set(ids) == {str(v.id) for v in (old, newer, negotiated, basic, yearly, euro)}
        assert str(theirs.id) not in ids
        by_id = {o["plan_version_id"]: o for o in body["options"]}
        assert by_id[str(newer.id)]["blocked_code"] is None
        assert by_id[str(negotiated.id)]["negotiated"] is True
        assert by_id[str(yearly.id)]["blocked_code"] == domain.CODE_CADENCE
        assert by_id[str(euro.id)]["blocked_code"] == domain.CODE_CURRENCY
        assert by_id[str(newer.id)]["version_no"] == 2
        assert body["current"]["plan_name"] == "Growth" and body["change_blocked_code"] is None

    async def test_only_this_organizations_negotiated_price_carries_an_amount(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """The owner's rule: pricing is agreed per client, not listed. A public version's amount —
        including the one the organization is on — is never sent; their own negotiated one is."""
        sub_id = await _subscribe(session, catalog, org_id, make_version(amount_minor=314159))
        public = catalog.add(make_version(amount_minor=271828))
        negotiated = catalog.add(
            make_version(amount_minor=161803, restricted_to_org_id=org_id, currency="USD")
        )
        _as(org_id)
        response = await client.get(f"/api/v1/subscriptions/{sub_id}/plan-options")
        assert "314159" not in response.text and "271828" not in response.text
        by_id = {o["plan_version_id"]: o for o in response.json()["options"]}
        assert by_id[str(public.id)]["amount_visible"] is False
        assert by_id[str(public.id)]["prices"] is None
        assert by_id[str(public.id)]["currency"] is None
        own = by_id[str(negotiated.id)]
        assert own["amount_visible"] is True and own["recurring_minor"] == 161803
        assert own["currency"] == "USD" and own["prices"][0]["amount_minor"] == 161803

        _as(org_id, [f"{MANAGE}:ORG", f"{READ}:ORG"])  # may change plans, may not read invoices
        withheld = await client.get(f"/api/v1/subscriptions/{sub_id}/plan-options")
        assert "161803" not in withheld.text

    async def test_a_waiting_change_is_marked_with_when_it_lands(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id = await _subscribe(session, catalog, org_id, make_version(amount_minor=1000))
        cheaper = catalog.add(make_version(amount_minor=500, plan_name="Lite"))
        _as(org_id)
        await _change(client, sub_id, cheaper.id)
        body = (await client.get(f"/api/v1/subscriptions/{sub_id}/plan-options")).json()
        sub = await _read(session, sub_id)
        assert body["pending"]["plan_name"] == "Lite"
        assert datetime.fromisoformat(body["pending_effective_at"]) == sub.current_period_end
        (row,) = [o for o in body["options"] if o["plan_version_id"] == str(cheaper.id)]
        assert row["is_pending"] is True


# ------------------------------------------------------------------------------ the plan line


class TestPlanSummaries:
    async def test_each_row_names_its_plan_and_a_scheduled_change(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        current = make_version(amount_minor=1000, plan_name="Revenue Growth", version_no=3)
        sub_id = await _subscribe(session, catalog, org_id, current)
        lite = catalog.add(make_version(amount_minor=500, plan_name="Revenue"))
        _as(org_id)
        await _change(client, sub_id, lite.id)
        (row,) = (await client.get("/api/v1/subscriptions/plan-summaries")).json()
        assert row["current"]["plan_name"] == "Revenue Growth"
        assert (row["current"]["version_no"], row["current"]["billing_interval"]) == (3, "month")
        assert row["pending"]["plan_name"] == "Revenue"
        sub = await _read(session, sub_id)
        assert datetime.fromisoformat(row["pending_effective_at"]) == sub.current_period_end
        assert row["current"]["amount_visible"] is False, "a public version is a list price"

    async def test_a_negotiated_price_is_shown_to_whoever_reads_invoices(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        negotiated = make_version(amount_minor=250000, restricted_to_org_id=org_id)
        await _subscribe(session, catalog, org_id, negotiated)
        _as(org_id)
        (row,) = (await client.get("/api/v1/subscriptions/plan-summaries")).json()
        assert row["current"]["negotiated"] is True
        assert row["current"]["recurring_minor"] == 250000
        assert row["pending"] is None and row["pending_effective_at"] is None
