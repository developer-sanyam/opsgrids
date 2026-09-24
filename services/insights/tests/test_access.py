"""The widget gate (ADR-0020 §4). No database — a pure function of a `Principal` and the
catalogue in `app/access.py`."""

from __future__ import annotations

import uuid

import pytest

from app import access
from platform_core.auth import Scope
from tests.helpers import principal_for


class TestMetricGate:
    def test_missing_entitlement_is_not_active(self, org_id: uuid.UUID) -> None:
        principal = principal_for(org_id, perms=["finance.reports.summary.read:ORG"])
        gate = access.gate_metric(principal, "revenue")
        assert gate.state == "not_active"
        assert gate.product_key == "finance"

    def test_missing_permission_is_no_access(self, org_id: uuid.UUID) -> None:
        principal = principal_for(org_id, entitlements=["finance"])
        gate = access.gate_metric(principal, "revenue")
        assert gate.state == "no_access"

    def test_org_scoped_grant_is_ok(self, org_id: uuid.UUID) -> None:
        principal = principal_for(
            org_id, entitlements=["finance"], perms=["finance.reports.summary.read:ORG"]
        )
        gate = access.gate_metric(principal, "revenue")
        assert gate.state == "ok"
        assert gate.scope is Scope.ORG

    def test_own_grant_on_an_org_only_metric_is_no_access(self, org_id: uuid.UUID) -> None:
        subject_ref = uuid.uuid4()
        principal = principal_for(
            org_id,
            subject_ref=subject_ref,
            entitlements=["finance"],
            perms=["finance.reports.summary.read:OWN"],
        )
        # `revenue` is ORG-only — an OWN grant is not enough (ADR-0020 §4).
        gate = access.gate_metric(principal, "revenue")
        assert gate.state == "no_access"

    def test_own_grant_on_an_own_capable_metric_is_ok(self, org_id: uuid.UUID) -> None:
        subject_ref = uuid.uuid4()
        principal = principal_for(
            org_id,
            subject_ref=subject_ref,
            entitlements=["crm"],
            perms=["crm.pipeline.deal.read:OWN"],
        )
        gate = access.gate_metric(principal, "deals_created")
        assert gate.state == "ok"
        assert gate.scope is Scope.OWN
        assert gate.subject_ref == subject_ref

    def test_team_narrows_to_own_never_widens(self, org_id: uuid.UUID) -> None:
        subject_ref = uuid.uuid4()
        principal = principal_for(
            org_id,
            subject_ref=subject_ref,
            entitlements=["crm"],
            perms=["crm.pipeline.deal.read:TEAM"],
        )
        gate = access.gate_metric(principal, "deals_created")
        assert gate.state == "ok"
        assert gate.scope is Scope.OWN

    def test_location_narrows_to_own(self, org_id: uuid.UUID) -> None:
        principal = principal_for(
            org_id,
            subject_ref=uuid.uuid4(),
            entitlements=["projects"],
            perms=["projects.board.task.read:LOCATION"],
        )
        gate = access.gate_metric(principal, "tasks_created")
        assert gate.state == "ok"
        assert gate.scope is Scope.OWN

    def test_no_grants_at_all_refuses(self, org_id: uuid.UUID) -> None:
        """Entitlement is checked first (ADR-0020 §4's own order), so a caller with neither the
        product nor the permission sees `not_active` — either way, nothing is revealed the
        caller does not already know about their own grants."""
        principal = principal_for(org_id)
        gate = access.gate_metric(principal, "revenue")
        assert gate.state == "not_active"

    def test_every_catalogue_metric_resolves_without_raising(self, org_id: uuid.UUID) -> None:
        principal = principal_for(org_id)
        for key in access.METRICS:
            gate = access.gate_metric(principal, key)
            assert gate.state in ("not_active", "no_access")

    def test_unknown_key_raises(self, org_id: uuid.UUID) -> None:
        principal = principal_for(org_id)
        with pytest.raises(KeyError):
            access.gate_metric(principal, "nonexistent_metric")


class TestBoardGate:
    def test_own_caller_gets_own_row_scope(self, org_id: uuid.UUID) -> None:
        subject_ref = uuid.uuid4()
        principal = principal_for(
            org_id,
            subject_ref=subject_ref,
            entitlements=["crm"],
            perms=["crm.pipeline.deal.read:OWN"],
        )
        gate = access.gate_board(principal, "sales_reps")
        assert gate.state == "ok"
        assert gate.scope is Scope.OWN

    def test_enquired_products_is_org_only(self, org_id: uuid.UUID) -> None:
        principal = principal_for(
            org_id,
            subject_ref=uuid.uuid4(),
            entitlements=["storefront"],
            perms=["storefront.sales.enquiry.read:OWN"],
        )
        gate = access.gate_board(principal, "enquired_products")
        assert gate.state == "no_access"


class TestActivityKindGate:
    def test_gated_per_kind(self, org_id: uuid.UUID) -> None:
        principal = principal_for(
            org_id, entitlements=["finance"], perms=["finance.billing.invoice.read:ORG"]
        )
        assert access.gate_activity_kind(principal, "invoice_issued").state == "ok"
        assert access.gate_activity_kind(principal, "payment_received").state == "no_access"

    def test_not_active_when_product_missing(self, org_id: uuid.UUID) -> None:
        principal = principal_for(org_id, perms=["finance.billing.invoice.read:ORG"])
        gate = access.gate_activity_kind(principal, "invoice_issued")
        assert gate.state == "not_active"
