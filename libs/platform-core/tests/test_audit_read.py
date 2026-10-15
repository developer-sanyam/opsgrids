"""The organization audit read's pure parts: which field *names* an entry reports, and that no
value ever survives into the response model. The database half runs per service
(`services/<name>/tests/test_org_audit_log.py`, through `platform_core.audit_read_testing`)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy.orm import DeclarativeBase

from platform_core.audit import AuditLogMixin
from platform_core.audit_read import (
    SENSITIVE_READ_ACTION,
    AuditFilters,
    audit_page_stmt,
    to_entry,
    touched_fields,
)


class _Base(DeclarativeBase):
    pass


class _AuditLog(_Base, AuditLogMixin):
    """A throwaway mapping, only ever compiled to SQL text."""


class TestTouchedFields:
    def test_a_reveal_names_the_opened_fields_and_nothing_else(self) -> None:
        after = {"fields": ["phone", "email"], "session_id": "sid-1", "amr": ["pwd", "otp"]}
        assert touched_fields(SENSITIVE_READ_ACTION, None, after) == ["email", "phone"]

    def test_a_reveal_with_no_field_list_names_nothing(self) -> None:
        assert touched_fields(SENSITIVE_READ_ACTION, None, {"fields": "phone"}) == []
        assert touched_fields(SENSITIVE_READ_ACTION, None, None) == []

    def test_an_update_names_only_the_keys_whose_value_changed(self) -> None:
        before = {"title": "Engineer", "phone": "+91 1", "city": "Pune"}
        after = {"title": "Lead", "phone": "+91 1", "city": "Pune"}
        assert touched_fields("contact.updated", before, after) == ["title"]

    def test_a_create_names_every_key_and_a_delete_every_removed_key(self) -> None:
        assert touched_fields("x.created", None, {"b": 1, "a": None}) == ["a", "b"]
        assert touched_fields("x.deleted", {"name": "n"}, None) == ["name"]

    def test_a_key_that_is_not_a_field_name_is_dropped_because_a_key_can_be_data(self) -> None:
        after = {"someone@example.com": "MEMBER", "Role": 1, "ok_field": 2}
        assert touched_fields("member.roles_set", None, after) == ["ok_field"]

    def test_request_metadata_keys_are_not_fields(self) -> None:
        assert touched_fields("x.updated", None, {"session_id": "s", "amr": [], "name": "n"}) == [
            "name"
        ]


class TestToEntry:
    def test_no_value_and_no_operator_identity_reach_the_model(self) -> None:
        row = SimpleNamespace(
            id=uuid.uuid4(),
            at=datetime(2026, 10, 1, tzinfo=UTC),
            actor_id=None,
            acting_as="operator:support@opsgrids.example",
            action="contact.updated",
            entity="contact",
            entity_id=uuid.uuid4(),
            before={"phone": "+91 98765 43210"},
            after={"phone": "+91 90000 00000"},
            reason=None,
            ip="10.0.0.1",
            user_agent="probe",
        )
        dumped = to_entry(row, service="customer", product="crm").model_dump_json()
        for leaked in ("98765", "90000", "support@opsgrids.example", "10.0.0.1", "probe"):
            assert leaked not in dumped
        entry = to_entry(row, service="customer", product="crm")
        assert entry.fields == ["phone"]
        assert entry.by_operator is True
        assert entry.sensitive is False

    def test_a_system_or_service_actor_is_not_opsgrids_staff(self) -> None:
        # `acting_as` also names system and service-to-service actors; only the `operator:`
        # prefix means staff acted. Anything else is a system action (no actor, not by staff).
        for acting_as in ("lead", "payment", "crm-conversion-cleanup", "system:notification", None):
            row = SimpleNamespace(
                id=uuid.uuid4(),
                at=datetime(2026, 10, 1, tzinfo=UTC),
                actor_id=None,
                acting_as=acting_as,
                action="deal.deleted",
                entity="deal",
                entity_id=uuid.uuid4(),
                before=None,
                after=None,
                reason=None,
            )
            entry = to_entry(row, service="deal", product="crm")
            assert entry.by_operator is False, acting_as
            assert entry.actor_id is None


class TestStatement:
    def test_the_organization_filter_is_in_the_query_itself(self) -> None:
        org = uuid.uuid4()
        stmt = audit_page_stmt(
            _AuditLog, organization_id=org, filters=AuditFilters(), after=None, limit=10
        )
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert f"organization_id = '{org.hex}'" in sql or f"organization_id = '{org}'" in sql
        assert "ORDER BY audit_logs.at DESC, audit_logs.id DESC" in sql
        assert "LIMIT 11" in sql
