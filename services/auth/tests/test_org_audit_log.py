"""`GET /api/v1/audit/auth`: the organization's sign-in trail.

`auth` is not run through the shared six-case contract as is: it returns only sign-in and password
rows, and returns them bare (actor, action, time). The same six checks are written here against
that rule, on the contract's plumbing (app role with RLS on, BYPASSRLS admin for the seed and the
cross-tenant selector run).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete

from app.api.audit import SIGN_IN_ACTIONS
from app.config import settings
from app.main import app
from app.models import AuditLog
from platform_core.audit_read import AUDIT_READ_PERMISSION, AuditFilters, audit_page_stmt
from platform_core.audit_read_testing import T0, AuditReadContract

pytestmark = pytest.mark.integration

SESSION_ID = uuid.uuid4()
SECRET = "198.51.100.77"


@pytest.fixture
def contract() -> AuditReadContract:
    return AuditReadContract(
        app=app, settings=settings(), audit_model=AuditLog, service="auth", product="core"
    )


def _row(org: uuid.UUID, actor: uuid.UUID, action: str, hours: int, **kw: object) -> AuditLog:
    return AuditLog(
        organization_id=org,
        actor_id=actor,
        action=action,
        entity="session",
        entity_id=SESSION_ID,
        ip=SECRET,
        user_agent="agent-secret/1.0",
        after={"session_id": str(SESSION_ID), "amr": ["pwd"]},
        reason="private-reason",
        at=T0 + timedelta(hours=hours),
        **kw,
    )


class _Seed:
    def __init__(self, contract: AuditReadContract) -> None:
        self.c = contract
        self.org, self.other = uuid.uuid4(), uuid.uuid4()
        self.alice, self.bob = uuid.uuid4(), uuid.uuid4()
        self.other_row = uuid.uuid4()

    async def __aenter__(self) -> _Seed:
        rows = [
            _row(self.org, self.alice, "auth.password_verified", 0),
            _row(self.org, self.alice, "auth.sso_signed_in", 1),
            _row(self.org, self.bob, "auth.password_change_failed", 2),
            _row(self.org, self.bob, "auth.oauth_signed_in", 3),
            _row(
                self.org, self.bob, "auth.password_changed", 4, acting_as="staff@opsgrids.example"
            ),
            # Not a sign-in row: must never be listed.
            _row(self.org, self.bob, "auth.mfa_enrolment_started", 5),
            _row(self.other, uuid.uuid4(), "auth.password_verified", 6, id=self.other_row),
        ]
        async with self.c._admin() as admin, admin.begin():
            admin.add_all(rows)
        return self

    async def __aexit__(self, *exc: object) -> None:
        async with self.c._admin() as admin, admin.begin():
            await admin.execute(
                delete(AuditLog).where(AuditLog.organization_id.in_([self.org, self.other]))
            )


class TestSignInAuditLog:
    async def test_lists_only_sign_in_rows_newest_first(self, contract: AuditReadContract) -> None:
        async with _Seed(contract) as s:
            response = await contract.get(contract.reader(s.org))
            assert response.status_code == 200, response.text
            items = response.json()["items"]
            assert len(items) == 5
            assert {i["action"] for i in items} <= SIGN_IN_ACTIONS
            ats = [i["at"] for i in items]
            assert ats == sorted(ats, reverse=True)

    async def test_refuses_a_caller_without_the_permission(
        self, contract: AuditReadContract
    ) -> None:
        async with _Seed(contract) as s:
            nobody = contract.principal(s.org, ["core.identity.membership.read:ORG"])
            assert (await contract.get(nobody)).status_code == 403
            own = contract.principal(s.org, [f"{AUDIT_READ_PERMISSION}:OWN"])
            assert (await contract.get(own)).status_code == 403

    async def test_never_returns_another_orgs_rows(self, contract: AuditReadContract) -> None:
        async with _Seed(contract) as s:
            mine = (await contract.get(contract.reader(s.org))).json()["items"]
            assert str(s.other_row) not in {i["id"] for i in mine}
            theirs = (await contract.get(contract.reader(s.other))).json()["items"]
            assert [i["id"] for i in theirs] == [str(s.other_row)]
            stmt = audit_page_stmt(
                AuditLog,
                organization_id=s.org,
                filters=AuditFilters(),
                after=None,
                limit=100,
                only_actions=SIGN_IN_ACTIONS,
            )
            async with contract._admin() as admin, admin.begin():
                rows = (await admin.execute(stmt)).scalars().all()
            assert rows and {r.organization_id for r in rows} == {s.org}

    async def test_rows_are_bare_actor_action_and_time(self, contract: AuditReadContract) -> None:
        async with _Seed(contract) as s:
            response = await contract.get(contract.reader(s.org))
            for leaked in (str(SESSION_ID), SECRET, "agent-secret", "private-reason", "staff@"):
                assert leaked not in response.text
            for item in response.json()["items"]:
                assert item["entity_id"] is None
                assert item["fields"] == []
                assert item["by_operator"] is False and item["sensitive"] is False
                assert item["reason"] is None
                assert "ip" not in item and "user_agent" not in item and "session_id" not in item
            assert response.json()["items"][0]["actor_id"] == str(s.bob)

    async def test_filters_and_pages(self, contract: AuditReadContract) -> None:
        async with _Seed(contract) as s:
            reader = contract.reader(s.org)
            by_actor = (await contract.get(reader, {"actor_id": str(s.alice)})).json()["items"]
            assert len(by_actor) == 2
            by_action = (await contract.get(reader, {"action": "auth.sso_signed_in"})).json()
            assert len(by_action["items"]) == 1
            # A filter cannot widen the allowlist.
            hidden = (await contract.get(reader, {"action": "auth.mfa_enrolment_started"})).json()
            assert hidden["items"] == []
            seen: list[str] = []
            cursor: str | None = None
            for _ in range(5):
                params = {"limit": "2", **({"cursor": cursor} if cursor else {})}
                page = (await contract.get(reader, params)).json()
                seen += [i["id"] for i in page["items"]]
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            assert len(seen) == 5 and len(set(seen)) == 5

    async def test_rejects_bad_input(self, contract: AuditReadContract) -> None:
        async with _Seed(contract) as s:
            reader = contract.reader(s.org)
            assert (await contract.get(reader, {"cursor": "not-a-cursor"})).status_code == 422
            backwards = {"from": (T0 + timedelta(days=1)).isoformat(), "to": T0.isoformat()}
            assert (await contract.get(reader, backwards)).status_code == 422
            assert (await contract.get(reader, {"from": "2026-10-01T00:00:00"})).status_code == 422
            assert (await contract.get(reader, {"limit": "500"})).status_code == 422
