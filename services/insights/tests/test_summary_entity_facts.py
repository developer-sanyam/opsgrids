"""I5/I6/I7 summary metrics (`app/metrics_entity_facts.py`) over real fact rows: the definitions
in `docs/insights/metrics.md` §4.3-4.5, each gate (`docs/insights/api.md` §8), OWN isolation, and
cross-tenant isolation under RLS.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailySnapshot, EnquiryFacts, PurchaseOrderFacts, TaskFacts
from tests.helpers import principal_for
from tests.test_api import as_principal

pytestmark = pytest.mark.integration

SUMMARY = "/api/v1/insights/summary"

PO_READ = "operations.procurement.purchase_order.read"
TASK_READ = "projects.board.task.read"
ENQUIRY_READ = "storefront.sales.enquiry.read"


def _po(
    organization_id: uuid.UUID,
    *,
    created_date: date,
    total_minor: int = 100_00,
    status: str = "draft",
    owner_ref: uuid.UUID | None = None,
    received_date: date | None = None,
    expected_date: date | None = None,
) -> PurchaseOrderFacts:
    now = datetime.now(UTC)
    return PurchaseOrderFacts(
        id=uuid.uuid4(),
        organization_id=organization_id,
        number=f"PO-{uuid.uuid4().hex[:6]}",
        status=status,
        supplier_ref=uuid.uuid4(),
        supplier_name="Meridian Metals",
        owner_ref=owner_ref,
        created_date=created_date,
        expected_date=expected_date or created_date + timedelta(days=14),
        received_date=received_date,
        currency="INR",
        total_minor=total_minor,
        source_created_at=now,
        source_updated_at=now,
    )


def _task(
    organization_id: uuid.UUID,
    *,
    stage: str = "backlog",
    owner_ref: uuid.UUID | None = None,
    completed_at: datetime | None = None,
    points: int = 3,
    due_date: date | None = None,
    source_created_at: datetime | None = None,
) -> TaskFacts:
    now = source_created_at or datetime.now(UTC)
    return TaskFacts(
        id=uuid.uuid4(),
        organization_id=organization_id,
        project_ref=uuid.uuid4(),
        title="A task",
        stage=stage,
        priority="medium",
        points=points,
        due_date=due_date,
        owner_ref=owner_ref,
        completed_at=completed_at,
        source_created_at=now,
        source_updated_at=now,
    )


def _enquiry(
    organization_id: uuid.UUID, *, source_created_at: datetime | None = None
) -> EnquiryFacts:
    now = source_created_at or datetime.now(UTC)
    return EnquiryFacts(
        id=uuid.uuid4(),
        organization_id=organization_id,
        store_ref=uuid.uuid4(),
        company="Shah Traders",
        status="new",
        line_count=1,
        source_created_at=now,
        source_updated_at=now,
    )


class TestPurchaseOrdersRaised:
    async def test_counts_only_non_cancelled_pos_created_in_the_window(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        today = date.today()
        session.add_all(
            [
                _po(org_id, created_date=today, status="draft"),
                _po(org_id, created_date=today, status="approved"),
                _po(org_id, created_date=today, status="cancelled"),  # excluded
                _po(
                    org_id, created_date=today - timedelta(days=90), status="draft"
                ),  # out of window
            ]
        )
        await session.commit()

        with as_principal(
            principal_for(org_id, entitlements=["operations"], perms=[f"{PO_READ}:ORG"])
        ):
            response = await client.get(
                SUMMARY, params={"metrics": "purchase_orders_raised", "period": "this_month"}
            )
        assert response.status_code == 200
        metric = response.json()["metrics"]["purchase_orders_raised"]
        assert metric["state"] == "ok"
        assert metric["value"] == 2

    async def test_missing_permission_is_no_access(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, entitlements=["operations"])):
            response = await client.get(SUMMARY, params={"metrics": "purchase_orders_raised"})
        assert response.json()["metrics"]["purchase_orders_raised"] == {"state": "no_access"}

    async def test_missing_entitlement_is_not_active(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, perms=[f"{PO_READ}:ORG"])):
            response = await client.get(SUMMARY, params={"metrics": "purchase_orders_raised"})
        body = response.json()["metrics"]["purchase_orders_raised"]
        assert body == {"state": "not_active", "product_key": "operations"}

    async def test_own_scope_sees_only_its_own_rows(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        me = uuid.uuid4()
        someone_else = uuid.uuid4()
        today = date.today()
        session.add_all(
            [
                _po(org_id, created_date=today, owner_ref=me),
                _po(org_id, created_date=today, owner_ref=me),
                _po(org_id, created_date=today, owner_ref=someone_else),
            ]
        )
        await session.commit()

        with as_principal(
            principal_for(
                org_id,
                subject_ref=me,
                entitlements=["operations"],
                perms=[f"{PO_READ}:OWN"],
            )
        ):
            response = await client.get(
                SUMMARY, params={"metrics": "purchase_orders_raised", "period": "this_month"}
            )
        metric = response.json()["metrics"]["purchase_orders_raised"]
        assert metric["state"] == "ok"
        assert metric["scope"] == "OWN"
        assert metric["value"] == 2


class TestOnTimeDelivery:
    async def test_percentage_of_pos_received_on_or_before_expected(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        today = date.today()
        session.add_all(
            [
                _po(
                    org_id,
                    created_date=today - timedelta(days=20),
                    status="received",
                    expected_date=today,
                    received_date=today,  # on time
                ),
                _po(
                    org_id,
                    created_date=today - timedelta(days=20),
                    status="received",
                    expected_date=today - timedelta(days=2),
                    received_date=today,  # late
                ),
            ]
        )
        await session.commit()

        with as_principal(
            principal_for(org_id, entitlements=["operations"], perms=[f"{PO_READ}:ORG"])
        ):
            response = await client.get(
                SUMMARY, params={"metrics": "on_time_delivery", "period": "this_month"}
            )
        metric = response.json()["metrics"]["on_time_delivery"]
        assert metric["state"] == "ok"
        assert metric["value"] == 50.0


class TestTasksMetrics:
    async def test_tasks_created_and_completed_and_points(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        today = datetime.now(UTC)
        session.add_all(
            [
                _task(org_id, source_created_at=today, points=5),
                _task(org_id, source_created_at=today, stage="done", completed_at=today, points=8),
                _task(
                    org_id,
                    source_created_at=today - timedelta(days=90),
                    stage="done",
                    completed_at=today - timedelta(days=90),
                    points=13,
                ),  # completed outside window
            ]
        )
        await session.commit()

        with as_principal(
            principal_for(org_id, entitlements=["projects"], perms=[f"{TASK_READ}:ORG"])
        ):
            response = await client.get(
                SUMMARY,
                params={
                    "metrics": "tasks_created,tasks_completed,task_points_completed",
                    "period": "this_month",
                },
            )
        body = response.json()["metrics"]
        assert body["tasks_created"]["value"] == 2
        assert body["tasks_completed"]["value"] == 1
        assert body["task_points_completed"]["value"] == 8

    async def test_overdue_tasks_is_current_with_no_comparison(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        yesterday = date.today() - timedelta(days=1)
        tomorrow = date.today() + timedelta(days=1)
        session.add_all(
            [
                _task(org_id, stage="backlog", due_date=yesterday),
                _task(org_id, stage="done", due_date=yesterday),  # done: not overdue
                _task(org_id, stage="backlog", due_date=tomorrow),  # not yet due
            ]
        )
        await session.commit()

        with as_principal(
            principal_for(org_id, entitlements=["projects"], perms=[f"{TASK_READ}:ORG"])
        ):
            response = await client.get(SUMMARY, params={"metrics": "overdue_tasks"})
        metric = response.json()["metrics"]["overdue_tasks"]
        assert metric["state"] == "ok"
        assert metric["kind"] == "current"
        assert metric["value"] == 1
        assert metric.get("comparison") is None


class TestEnquiriesReceived:
    async def test_org_only_counts_enquiries_created_in_the_window(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        now = datetime.now(UTC)
        session.add_all(
            [_enquiry(org_id, source_created_at=now), _enquiry(org_id, source_created_at=now)]
        )
        await session.commit()

        with as_principal(
            principal_for(org_id, entitlements=["storefront"], perms=[f"{ENQUIRY_READ}:ORG"])
        ):
            response = await client.get(
                SUMMARY, params={"metrics": "enquiries_received", "period": "this_month"}
            )
        metric = response.json()["metrics"]["enquiries_received"]
        assert metric["state"] == "ok"
        assert metric["scope"] == "ORG"
        assert metric["value"] == 2

    async def test_own_scope_is_no_access_org_only_is_the_permissions_own_maximum(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(
            principal_for(
                org_id,
                subject_ref=uuid.uuid4(),
                entitlements=["storefront"],
                perms=[f"{ENQUIRY_READ}:OWN"],
            )
        ):
            response = await client.get(SUMMARY, params={"metrics": "enquiries_received"})
        assert response.json()["metrics"]["enquiries_received"] == {"state": "no_access"}


class TestSnapshotBackedStateMetrics:
    async def test_open_tasks_reads_the_latest_capture(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        session.add(
            DailySnapshot(
                organization_id=org_id,
                metric="open_tasks",
                dimension="",
                day=date.today(),
                value=7,
                currency=None,
                captured_at=datetime.now(UTC),
            )
        )
        await session.commit()

        with as_principal(
            principal_for(org_id, entitlements=["projects"], perms=[f"{TASK_READ}:ORG"])
        ):
            response = await client.get(SUMMARY, params={"metrics": "open_tasks"})
        metric = response.json()["metrics"]["open_tasks"]
        assert metric["state"] == "ok"
        assert metric["value"] == 7

    async def test_no_capture_yet_is_insufficient_history_not_a_fake_zero(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(
            principal_for(org_id, entitlements=["projects"], perms=[f"{TASK_READ}:ORG"])
        ):
            response = await client.get(SUMMARY, params={"metrics": "open_tasks"})
        metric = response.json()["metrics"]["open_tasks"]
        assert metric["state"] == "insufficient_history"
        assert metric.get("value") is None


class TestCrossTenantIsolation:
    async def test_another_organizations_purchase_orders_never_count(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        today = date.today()
        # Mine.
        session.add(_po(org_id, created_date=today))
        await session.commit()
        # A different organization's row, seeded on the bypass role — RLS is the net even if a
        # future selector ever forgot its own explicit filter (CLAUDE.md).
        admin_session.add(_po(other_org, created_date=today))
        await admin_session.commit()

        with as_principal(
            principal_for(org_id, entitlements=["operations"], perms=[f"{PO_READ}:ORG"])
        ):
            response = await client.get(
                SUMMARY, params={"metrics": "purchase_orders_raised", "period": "this_month"}
            )
        assert response.json()["metrics"]["purchase_orders_raised"]["value"] == 1
