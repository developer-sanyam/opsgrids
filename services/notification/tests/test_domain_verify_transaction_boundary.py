"""D3: `POST /engage/domains/{id}/verify` must not hold a transaction while DNS is being asked.

`services.verify_domain` used to run `dns_check.check_domain_records` inside the one transaction
the request's chokepoint opened for the whole handler — the docstring even argued for it. These
tests prove the restructured shape instead: `load_domain_for_check` (a short transaction), the
lookups themselves with no session open at all, then `record_domain_check` (a second short
transaction). Unlike a portal charge or an e-invoice filing, a crash between the lookup and
recording it leaves nothing inconsistent — DNS is read-only from this service's point of view —
so there is no reconciler here, only the property that no transaction spans the network call.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from typing import Any

import dns.exception
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import dns_check, models, services
from app.main import app
from platform_core.auth import Principal, Scope, get_principal
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration

DOMAINS = "/api/v1/engage/domains"


class FakeResolver:
    """Answers from what the test wrote down — the same shape `test_engage_identity.py` uses."""

    def __init__(self, answers: dict[tuple[str, str], list[str]]) -> None:
        self._answers = answers

    async def resolve(self, host: str, rtype: str) -> list[str]:
        return self._answers.get((host, rtype), [])


@contextlib.contextmanager
def _as(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _engage_principal(org_id: uuid.UUID) -> Principal:
    return principal_for(org_id, ["engage.setup.domain.write:ORG"], entitlements=["engage"])


async def _seed_domain(session: AsyncSession, org_id: uuid.UUID) -> models.EmailDomain:
    """A domain behind a connected Google Workspace connection — verification needs to know how
    the organization sends before it has anything to check DNS against at all."""
    domain_ctx = ctx_for(org_id, "engage.setup.domain.write", Scope.ORG)
    connection_ctx = ctx_for(org_id, "engage.setup.connection.write", Scope.ORG)
    async with session.begin():
        await services.create_connection(
            session,
            connection_ctx,
            kind="google",
            username="info@company.com",
            secret="app-password",  # pragma: allowlist secret
        )
        row = await services.create_domain(session, domain_ctx, domain="company.com")
    return row


class TestNoTransactionDuringTheDnsLookup:
    async def test_the_route_holds_no_transaction_while_dns_is_checked(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        row = await _seed_domain(session, org_id)

        seen_in_transaction: list[bool] = []
        original_check = dns_check.check_domain_records

        async def _spy(domain_name: str, **kwargs: Any) -> object:
            seen_in_transaction.append(session.in_transaction())
            return await original_check(domain_name, **kwargs)

        import app.api.engage as engage_module

        monkeypatch.setattr(engage_module.dns_check, "check_domain_records", _spy)

        with _as(_engage_principal(org_id)):
            response = await client.post(f"{DOMAINS}/{row.id}/verify")

        assert response.status_code == 200, response.text
        assert seen_in_transaction == [False], (
            "DNS was checked while a transaction was open on the request's session"
        )

    async def test_a_verified_answer_is_still_recorded_correctly(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        row = await _seed_domain(session, org_id)
        published = {
            ("company.com", "TXT"): ["v=spf1 include:_spf.google.com ~all"],
            ("google._domainkey.company.com", "TXT"): ["v=DKIM1; k=rsa; p=AAA"],
        }

        import app.api.engage as engage_module

        original_check = dns_check.check_domain_records

        async def _fake_check(
            domain_name: str, *, resolver: object = None, **kwargs: Any
        ) -> object:
            return await original_check(domain_name, resolver=FakeResolver(published), **kwargs)

        monkeypatch.setattr(engage_module.dns_check, "check_domain_records", _fake_check)

        with _as(_engage_principal(org_id)):
            response = await client.post(f"{DOMAINS}/{row.id}/verify")

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "verified"


class TestUnaffectedByAnUnreachableResolver:
    async def test_a_resolver_failure_leaves_the_domain_untouched(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The crash-equivalent case: the lookup phase raises before `record_domain_check` ever
        runs. There is nothing to reconcile — the claim transaction (`load_domain_for_check`)
        wrote nothing at all — so the domain is left exactly as it was before the attempt."""
        row = await _seed_domain(session, org_id)
        before_checked_at = row.last_checked_at

        import app.api.engage as engage_module

        async def _boom(domain_name: str, **kwargs: Any) -> object:
            raise dns.exception.Timeout()

        monkeypatch.setattr(engage_module.dns_check, "check_domain_records", _boom)

        with _as(_engage_principal(org_id)), pytest.raises(dns.exception.Timeout):
            await client.post(f"{DOMAINS}/{row.id}/verify")

        async with session.begin():
            from app import selectors

            reread = await selectors.get_domain(session, org_id, row.id)
        assert reread is not None
        assert reread.last_checked_at == before_checked_at
        assert reread.status == "pending"
