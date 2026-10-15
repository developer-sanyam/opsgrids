"""`GET /api/v1/audit/finance`: this service's slice of the organization audit log.

The checks are the shared contract (`platform_core.audit_read_testing`), run here against this
service's own schema and app, as the app role with RLS on. Negative authorization, the
cross-tenant read (including the selector run as BYPASSRLS), reveal rows that name fields but
never values, filters, paging and bad input.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.main import app
from app.models import AuditLog
from platform_core.audit_read_testing import AuditReadContract

pytestmark = pytest.mark.integration


@pytest.fixture
def contract() -> AuditReadContract:
    return AuditReadContract(
        app=app, settings=settings(), audit_model=AuditLog, service="finance", product="finance"
    )


class TestOrganizationAuditLog:
    async def test_lists_the_callers_org_newest_first(self, contract: AuditReadContract) -> None:
        await contract.check_lists_the_callers_org_newest_first()

    async def test_refuses_a_caller_without_the_permission(
        self, contract: AuditReadContract
    ) -> None:
        await contract.check_refuses_a_caller_without_the_permission()

    async def test_never_returns_another_orgs_rows(self, contract: AuditReadContract) -> None:
        await contract.check_never_returns_another_orgs_rows()

    async def test_a_reveal_shows_who_what_and_when_but_never_a_value(
        self, contract: AuditReadContract
    ) -> None:
        await contract.check_a_reveal_shows_who_what_and_when_but_never_a_value()

    async def test_filters_and_pages(self, contract: AuditReadContract) -> None:
        await contract.check_filters_and_pages()

    async def test_rejects_bad_input(self, contract: AuditReadContract) -> None:
        await contract.check_rejects_bad_input()
