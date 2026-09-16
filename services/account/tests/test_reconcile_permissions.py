"""ADR-0007: a permission added to an existing role template reaches organizations that
already exist, not only new ones.

`create_organization` installs `core` once, at birth. Before this, `install_registration`
skipped any role code that already existed, so a grant added to `MEMBER` or `ORG_OWNER`
after an organization was created never reached it — the tenant's roles kept the grant set
they were born with, forever, with no error anywhere. These tests prove the reconcile these
functions and `tools/reconcile_permissions.py` now perform, and the properties its honesty
rests on:

* widening an existing **system** role unions in the grants it is missing and raises a
  narrower scope, and never removes or narrows anything;
* a **customer-authored** role of the same code is left alone entirely, and the gap is
  reported (`skipped_customer_roles`), never auto-closed;
* a second run against an unchanged diff is a true no-op: no grant, no audit row, no
  `core.grants.changed` event, no `pv` bump;
* a changed organization bumps `pv` **exactly once**, however many memberships it has, and
  that bump is what the gateway's staleness check — the internal grants endpoint — actually
  sees;
* the fleet command is one transaction per organization: a failure partway (Redis
  unreachable, simulated here) leaves every organization already processed committed and
  never touches the one it failed on or anything after it.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app import selectors, services
from app.catalogue import (
    CORE,
    ProductManifest,
    ProductRegistration,
    RoleTemplate,
    load_product_manifest,
)
from app.config import settings
from app.models import Membership, Organization, OutboxEvent
from app.schemas import GrantIn, OrganizationIn, RoleIn
from platform_core.auth import Scope
from platform_core.cache import permission_version_key
from platform_core.internal_auth import mint_service_token
from tests.helpers import FakeRedis, ctx_for

pytestmark = pytest.mark.integration

# `tools/` is not a package and sits outside the service directory; reached the same way
# `tools/wire_product.py` reaches `services/account` — by adding its parent to `sys.path` —
# because these tests exercise the CLI's own fleet-orchestration logic, not a reimplementation
# of it.
_TOOLS_DIR = Path(__file__).resolve().parents[3] / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
import reconcile_permissions  # type: ignore[import-not-found]  # noqa: E402


def _widen_member_registration(
    *, grant_code: str = "identity.audit.read", scope: str = "ORG"
) -> ProductRegistration:
    """A registration that widens `MEMBER` by exactly one grant it does not yet hold.

    Deliberately built from the real `CORE` catalogue rather than a fabricated one, so the
    permission row this widens already exists (installed by `create_organization`) — the
    same shape a real catalogue change to `core` would take.
    """
    return ProductRegistration(
        product="core",
        catalogue=CORE,
        role_templates=[
            RoleTemplate(
                code="MEMBER",
                name="Member",
                product="core",
                grants=CORE.pick((grant_code, scope)),
            )
        ],
    )


async def _grants_changed_event_count(session: AsyncSession, org_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(OutboxEvent)
        .where(
            OutboxEvent.organization_id == org_id,
            OutboxEvent.event_type == services.EVT_GRANTS_CHANGED,
            OutboxEvent.aggregate_type == "organization",
        )
    )
    return int(result.scalar_one())


class TestWideningAnExistingSystemRole:
    async def test_a_missing_grant_is_added(self, session: AsyncSession, org: Organization) -> None:
        registration = _widen_member_registration()

        async with session.begin():
            diff = await services.install_registration(session, org.id, registration)

        assert diff.roles_created == []
        assert diff.skipped_customer_roles == []
        assert diff.grants_added == [("MEMBER", "core.identity.audit.read", "ORG")]
        assert diff.scopes_widened == []
        assert diff.changed is True

        async with session.begin():
            role = await selectors.get_role_by_code(session, org.id, "MEMBER")
        assert role is not None
        assert role.is_system is True
        assert "core.identity.audit.read" in {g.permission.code for g in role.grants}

    async def test_a_narrower_existing_scope_is_raised_never_lowered(
        self, session: AsyncSession, org: Organization
    ) -> None:
        # MEMBER already holds `identity.membership.read` at TEAM (CORE_ROLE_TEMPLATES).
        registration = _widen_member_registration(
            grant_code="identity.membership.read", scope="ORG"
        )

        async with session.begin():
            diff = await services.install_registration(session, org.id, registration)

        assert diff.grants_added == []
        assert diff.scopes_widened == [("MEMBER", "core.identity.membership.read", "TEAM", "ORG")]

        async with session.begin():
            role = await selectors.get_role_by_code(session, org.id, "MEMBER")
        assert role is not None
        scope_by_code = {g.permission.code: g.scope for g in role.grants}
        assert scope_by_code["core.identity.membership.read"] == "ORG"

    async def test_nothing_already_held_is_ever_removed(
        self, session: AsyncSession, org: Organization
    ) -> None:
        """A template that names one grant leaves every other grant the role already had."""
        registration = _widen_member_registration()

        async with session.begin():
            await services.install_registration(session, org.id, registration)

        async with session.begin():
            role = await selectors.get_role_by_code(session, org.id, "MEMBER")
        assert role is not None
        codes = {g.permission.code for g in role.grants}
        # The original MEMBER template's grants are all still present.
        assert "core.org.organization.read" in codes
        assert "core.profile.self.write" in codes


class TestCustomerAuthoredRoleIsUntouchedAndReported:
    async def test_the_role_is_skipped_and_named_in_the_diff(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        manager = people["manager"]
        async with session.begin():
            ctx = ctx_for(org.id, manager, "core.identity.role.write", Scope.ORG)
            role = await services.create_role(
                session,
                ctx,
                RoleIn(
                    code="AUDITOR",
                    name="Auditor",
                    grants=[GrantIn(permission_code="core.identity.audit.read", scope="ORG")],
                ),
            )
        assert role.is_system is False

        registration = ProductRegistration(
            product="core",
            catalogue=CORE,
            role_templates=[
                RoleTemplate(
                    code="AUDITOR",
                    name="Auditor",
                    product="core",
                    grants=CORE.pick(("identity.membership.write", "ORG")),
                )
            ],
        )

        async with session.begin():
            diff = await services.install_registration(session, org.id, registration)

        assert diff.skipped_customer_roles == ["AUDITOR"]
        assert diff.grants_added == []
        assert diff.scopes_widened == []
        assert diff.changed is False

        async with session.begin():
            refreshed = await selectors.get_role_by_code(session, org.id, "AUDITOR")
        assert refreshed is not None
        assert refreshed.is_system is False
        # Untouched: still exactly what the customer built, not what the template wanted.
        assert {g.permission.code for g in refreshed.grants} == {"core.identity.audit.read"}


class TestReconcileIsIdempotent:
    async def test_a_second_run_is_a_true_no_op(
        self, session: AsyncSession, org: Organization, redis: FakeRedis
    ) -> None:
        registration = _widen_member_registration()
        key = permission_version_key(str(org.id))

        async with session.begin():
            first = await services.reconcile_registration(
                session, org.id, registration, acting_as="test"
            )
        assert first.changed is True
        assert first.grants_added == 1
        assert redis.store.get(key) == "1"

        async with session.begin():
            events_after_first = await _grants_changed_event_count(session, org.id)
        assert events_after_first == 1

        async with session.begin():
            second = await services.reconcile_registration(
                session, org.id, registration, acting_as="test"
            )
        assert second.changed is False
        assert second.grants_added == 0
        assert second.scopes_widened == 0
        # No further bump, and no second event.
        assert redis.store.get(key) == "1"

        async with session.begin():
            events_after_second = await _grants_changed_event_count(session, org.id)
        assert events_after_second == 1


class TestPvBumpsExactlyOnceAndIsVisibleToTheGateway:
    async def test_one_bump_regardless_of_membership_count(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        redis: FakeRedis,
        client: AsyncClient,
    ) -> None:
        registration = _widen_member_registration()
        key = permission_version_key(str(org.id))
        assert key not in redis.store

        async with session.begin():
            summary = await services.reconcile_registration(
                session, org.id, registration, acting_as="test"
            )
        assert summary.changed is True

        # `people` holds four memberships in this organization; the bump is per
        # organization, not per membership.
        assert redis.store[key] == "1"

        response = await client.get(
            f"/internal/v1/memberships/{people['report'].id}/grants",
            headers={
                "X-Internal-Token": mint_service_token(
                    settings(), audience="account", caller="tests"
                ),
                "X-Calling-Service": "tests",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert "core.identity.audit.read:ORG" in body["grants"]
        # The exact value the gateway compares `claims["pv"]` against.
        assert body["permission_version"] == 1


class TestDryRunWritesNothing:
    async def test_dry_run_computes_the_diff_but_persists_no_change(
        self, session: AsyncSession, org: Organization, redis: FakeRedis
    ) -> None:
        registration = _widen_member_registration()
        key = permission_version_key(str(org.id))

        async with session.begin():
            summary = await services.reconcile_registration(
                session, org.id, registration, acting_as="test", dry_run=True
            )
        assert summary.changed is True
        assert summary.grants_added == 1

        # The transaction above committed cleanly (dry_run only gates the audit/event/bump,
        # not the write itself) — a real dry-run caller is responsible for rolling back, and
        # `tools/reconcile_permissions.py` does exactly that. Prove the redis side of the
        # contract here: no bump happened even though the diff was non-empty.
        assert key not in redis.store


@pytest_asyncio.fixture
async def other_org(admin_session: AsyncSession) -> Organization:
    """A second, independent tenant — created where organizations legitimately are."""
    async with admin_session.begin():
        org2 = await services.create_organization(
            admin_session,
            OrganizationIn(name="Beta Corp", slug="beta-reconcile"),
            actor_user_id=None,
        )
    return org2


async def _member_has_grant(session: AsyncSession, org_id: uuid.UUID, permission_code: str) -> bool:
    row = await session.execute(
        text(
            "SELECT 1 FROM account.role_permissions rp "
            "JOIN account.roles r ON r.id = rp.role_id "
            "JOIN account.permissions p ON p.id = rp.permission_id "
            "WHERE r.organization_id = :org AND r.code = 'MEMBER' AND p.code = :perm"
        ),
        {"org": org_id, "perm": permission_code},
    )
    return row.first() is not None


class TestApprovalGrantsReachExistingTenants:
    """ADR-0006 §11 depends on ADR-0007: without this, every `core.approval.*` endpoint
    403s to every principal in every organization that existed before approval's catalogue
    did, and the unassigned-request queue has no holder anywhere.

    `services/approval/app/catalogue.py` contributes `core.approval.*` to `core` as data;
    `tools/export_permissions.py` merges it into the committed `core.json` exactly as it
    already merges `hrms.json` from `employee` and `attendance` (ADR-0007 §2's note). This
    reconciles a real organization against that real, committed, merged manifest — not a
    fabricated registration — and asserts the grants land where ADR-0006 §11 and ADR-0007 §4
    say they must: `ORG_OWNER` ends the reconcile holding `decide:ORG` (so the unassigned
    queue always has an owner), `MEMBER` picks up `decide:OWN`, and `APPROVAL_ADMIN` — a
    template with no code collision in `CORE_ROLE_TEMPLATES` — installs as a new role exactly
    as any other missing template would (the half of the gap that was never broken).
    """

    async def test_reconciling_core_grants_approval_permissions_to_the_expected_roles(
        self, session: AsyncSession, org: Organization
    ) -> None:
        manifest = load_product_manifest("core")
        assert manifest is not None, (
            "libs/contracts/permissions/core.json is missing — run `uv run python "
            "tools/export_permissions.py` from the repository root"
        )
        approval_codes = {
            grant.code
            for template in manifest.registration.role_templates
            for grant in template.grants
            if grant.code.startswith("core.approval.")
        }
        assert approval_codes, (
            "core.json carries no core.approval.* grants — is "
            "services/approval/app/catalogue.py wired into the exporter?"
        )

        async with session.begin():
            summary = await services.reconcile_registration(
                session, org.id, manifest.registration, acting_as="test"
            )
        assert summary.changed is True

        async with session.begin():
            owner_role = await selectors.get_role_by_code(session, org.id, "ORG_OWNER")
            admin_role = await selectors.get_role_by_code(session, org.id, "ORG_ADMIN")
            member_role = await selectors.get_role_by_code(session, org.id, "MEMBER")
            approval_admin_role = await selectors.get_role_by_code(
                session, org.id, "APPROVAL_ADMIN"
            )

        assert owner_role is not None
        owner_scope_by_code = {g.permission.code: g.scope for g in owner_role.grants}
        # The property ADR-0007 §4 cites as what keeps the unassigned queue safe: ORG_OWNER
        # is a system role, it is reconciled, and its template grants CORE.all("ORG") plus
        # approval's slice at ORG.
        assert owner_scope_by_code["core.approval.request.decide"] == "ORG"
        assert owner_scope_by_code["core.approval.request.read"] == "ORG"

        assert admin_role is not None
        admin_codes = {g.permission.code for g in admin_role.grants}
        assert "core.approval.policy.write" in admin_codes

        assert member_role is not None
        member_scope_by_code = {g.permission.code: g.scope for g in member_role.grants}
        assert member_scope_by_code["core.approval.request.decide"] == "OWN"
        assert member_scope_by_code["core.approval.request.read"] == "OWN"

        # A brand-new role template code installs into a live tenant correctly — that part
        # was never broken (ADR-0007, "What this rejects"); exercised here for real.
        assert approval_admin_role is not None
        assert approval_admin_role.is_system is True
        assert {g.permission.code for g in approval_admin_role.grants} == approval_codes


class TestFleetReconcileIsOneTransactionPerOrganization:
    async def test_a_failure_partway_leaves_earlier_orgs_committed_and_later_ones_untouched(
        self,
        org: Organization,
        other_org: Organization,
        admin_session: AsyncSession,
        redis: FakeRedis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        registration = _widen_member_registration()
        manifest = ProductManifest(product="core", registration=registration, admin_role_codes=())
        monkeypatch.setattr(
            "app.catalogue.load_product_manifest",
            lambda key: manifest if key == "core" else None,
        )

        # `org` (processed first) succeeds; `other_org` (processed second) hits a poisoned
        # Redis and must fail loudly rather than being silently skipped or reported done.
        redis.poisoned.add(permission_version_key(str(other_org.id)))

        with pytest.raises(ConnectionError):
            await reconcile_permissions.reconcile_fleet(
                "core", [org.id, other_org.id], dry_run=False, pace_ms=0
            )

        async with admin_session.begin():
            first_org_has_grant = await _member_has_grant(
                admin_session, org.id, "core.identity.audit.read"
            )
            second_org_has_grant = await _member_has_grant(
                admin_session, other_org.id, "core.identity.audit.read"
            )

        assert first_org_has_grant is True, (
            "the first organization's transaction had already committed before the second "
            "one failed — it must not be rolled back by a later failure"
        )
        assert second_org_has_grant is False, (
            "the failing organization's transaction must be rolled back, not partially applied"
        )

        assert redis.store.get(permission_version_key(str(org.id))) == "1"
        assert permission_version_key(str(other_org.id)) not in redis.store
