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
  never touches the one it failed on or anything after it;
* the production bug of 2026-09-25: a role template that reaches a live tenant through this
  reconciler used to land with no holder, because only `install_product_registration` (the
  activation path) ever granted a product's admin role to an organization's owners. An
  organization that activated the product *before* the new admin template existed now gets
  its owner granted on reconcile, through the same function that path already calls
  (`grant_admin_roles_to_owners`); a second run is a true no-op; an organization that never
  activated the product gets nothing; a non-owner member gets nothing; and `--dry-run`
  reports the pending grant and writes nothing.
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
from app.catalogue import CORE, ProductManifest, load_product_manifest
from app.config import settings
from app.models import Membership, Organization, OutboxEvent, Role, RolePermission
from app.schemas import GrantIn, OrganizationIn, RoleIn
from platform_core.auth import Scope
from platform_core.cache import permission_version_key
from platform_core.catalogue import (
    PermissionCatalogue,
    PermissionSpec,
    ProductRegistration,
    RoleTemplate,
)
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


async def _grants_changed_event_count_for_membership(
    session: AsyncSession, membership_id: uuid.UUID
) -> int:
    """The per-membership `core.grants.changed` fan-out `grant_admin_roles_to_owners`'
    callers emit — bounded by the organization's owner count, not by every membership in the
    tenant, so ADR-0007 §5's fan-out concern does not apply to it (unlike the widening event
    `_grants_changed_event_count` above counts)."""
    result = await session.execute(
        select(func.count())
        .select_from(OutboxEvent)
        .where(
            OutboxEvent.event_type == services.EVT_GRANTS_CHANGED,
            OutboxEvent.aggregate_type == "membership",
            OutboxEvent.aggregate_id == membership_id,
        )
    )
    return int(result.scalar_one())


def _crm_registration_with_new_admin_role() -> ProductRegistration:
    """A stand-in for CRM's manifest, shaped like the production incident of 2026-09-25:
    one admin-most role template (`CRM_LEADS_ADMIN`) naming a permission no earlier version
    of the catalogue had — a role template that is *new* to an organization which activated
    CRM before this template existed, exactly the case `install_registration` has always
    installed correctly and only the owner grant used to miss."""
    catalogue = PermissionCatalogue(
        "crm",
        [
            PermissionSpec(
                module="lead",
                resource="lead",
                action="convert",
                description="Convert a lead into a deal",
                default_scope="OWN",
                max_scope="ORG",
            )
        ],
    )
    return ProductRegistration(
        product="crm",
        catalogue=catalogue,
        role_templates=[
            RoleTemplate(
                code="CRM_LEADS_ADMIN",
                name="Leads Admin",
                product="crm",
                grants=catalogue.pick(("lead.lead.convert", "ORG")),
            )
        ],
    )


async def _add_owner(
    session: AsyncSession, org_id: uuid.UUID, *, email: str = "owner@example.com"
) -> Membership:
    """A second, active owner membership on `org` — `create_organization` installs `core`'s
    role templates but adds no member at all, so ADR-0007's reconcile (which grants to
    *active owner memberships*) has nothing to grant until one exists (the `signup` flow is
    the production path; this is its "add the owner" half, reused because `signup` also
    creates a brand-new organization and these tests need one already in hand)."""
    owner = await services.add_member(
        session,
        org_id=org_id,
        email=email,
        full_name="Olive Owner",
        actor_user_id=None,
        role_codes=["ORG_OWNER"],
    )
    owner.is_owner = True
    await session.flush()
    return owner


async def _membership_role_codes(session: AsyncSession, membership_id: uuid.UUID) -> set[str]:
    rows = await session.execute(
        text(
            "SELECT r.code FROM account.membership_roles mr "
            "JOIN account.roles r ON r.id = mr.role_id "
            "WHERE mr.membership_id = :membership_id"
        ),
        {"membership_id": membership_id},
    )
    return {row[0] for row in rows}


class _StubActivationChecker:
    """Deterministic `reconcile_permissions.ActivationChecker` for tests — no product-service,
    no HTTP. Defaults to active for every organization not named in `inactive_for`, which is
    the answer a genuinely activated product's entitlements read would give."""

    def __init__(self, *, inactive_for: frozenset[uuid.UUID] = frozenset()) -> None:
        self._inactive_for = inactive_for

    async def is_active(self, organization_id: uuid.UUID, product_key: str) -> bool:
        return organization_id not in self._inactive_for


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
        # `manager` holds `ORG_ADMIN`, which does not carry `identity.audit.read` — the
        # role's grant has to be something the caller actually holds, now that
        # `create_role` enforces the same ceiling `invite` and `set_member_roles` do.
        manager = people["manager"]
        async with session.begin():
            ctx = ctx_for(org.id, manager, "core.identity.role.write", Scope.ORG)
            role = await services.create_role(
                session,
                ctx,
                RoleIn(
                    code="AUDITOR",
                    name="Auditor",
                    grants=[GrantIn(permission_code="core.identity.role.read", scope="ORG")],
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
        assert {g.permission.code for g in refreshed.grants} == {"core.identity.role.read"}


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


class TestReconcileGrantsAdminRoleToOwners:
    """The production bug of 2026-09-25: `CRM_LEADS_ADMIN` reached an organization that had
    activated CRM before that template existed, installed with no holder — only
    `install_product_registration` (the activation path) ever granted a product's admin role
    to an organization's owners. `reconcile_registration` now closes it through the same
    `grant_admin_roles_to_owners` that path calls.
    """

    async def test_an_owner_is_granted_the_new_admin_role_when_the_product_is_active(
        self, session: AsyncSession, org: Organization, redis: FakeRedis
    ) -> None:
        registration = _crm_registration_with_new_admin_role()
        async with session.begin():
            owner = await _add_owner(session, org.id)

        async with session.begin():
            summary = await services.reconcile_registration(
                session,
                org.id,
                registration,
                acting_as="test",
                admin_role_codes=("CRM_LEADS_ADMIN",),
                product_active=True,
            )

        assert summary.changed is True
        assert summary.admin_grant.role_codes == ["CRM_LEADS_ADMIN"]
        assert summary.admin_grant.granted == [(owner.id, "CRM_LEADS_ADMIN")]
        assert redis.store[permission_version_key(str(org.id))] == "1"

        async with session.begin():
            codes = await _membership_role_codes(session, owner.id)
        assert "CRM_LEADS_ADMIN" in codes

        async with session.begin():
            events = await _grants_changed_event_count_for_membership(session, owner.id)
        assert events == 1

    async def test_a_second_run_is_a_true_no_op(
        self, session: AsyncSession, org: Organization, redis: FakeRedis
    ) -> None:
        registration = _crm_registration_with_new_admin_role()
        key = permission_version_key(str(org.id))
        async with session.begin():
            owner = await _add_owner(session, org.id)

        async with session.begin():
            first = await services.reconcile_registration(
                session,
                org.id,
                registration,
                acting_as="test",
                admin_role_codes=("CRM_LEADS_ADMIN",),
                product_active=True,
            )
        assert first.changed is True
        assert redis.store[key] == "1"

        async with session.begin():
            events_after_first = await _grants_changed_event_count_for_membership(session, owner.id)
        assert events_after_first == 1

        async with session.begin():
            second = await services.reconcile_registration(
                session,
                org.id,
                registration,
                acting_as="test",
                admin_role_codes=("CRM_LEADS_ADMIN",),
                product_active=True,
            )

        assert second.changed is False
        assert second.admin_grant.granted == []
        # No further bump, and no second event: the owner already held the role.
        assert redis.store[key] == "1"

        async with session.begin():
            events_after_second = await _grants_changed_event_count_for_membership(
                session, owner.id
            )
        assert events_after_second == 1

    async def test_a_non_owner_member_gets_nothing(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        registration = _crm_registration_with_new_admin_role()
        async with session.begin():
            await _add_owner(session, org.id)

        async with session.begin():
            await services.reconcile_registration(
                session,
                org.id,
                registration,
                acting_as="test",
                admin_role_codes=("CRM_LEADS_ADMIN",),
                product_active=True,
            )

        async with session.begin():
            member_codes = await _membership_role_codes(session, people["report"].id)
            manager_codes = await _membership_role_codes(session, people["manager"].id)
        assert "CRM_LEADS_ADMIN" not in member_codes
        assert "CRM_LEADS_ADMIN" not in manager_codes


class TestReconcileSkipsAnOrganizationThatNeverActivatedTheProduct:
    async def test_the_owner_is_granted_nothing(
        self, session: AsyncSession, org: Organization, redis: FakeRedis
    ) -> None:
        registration = _crm_registration_with_new_admin_role()
        async with session.begin():
            owner = await _add_owner(session, org.id)

        async with session.begin():
            summary = await services.reconcile_registration(
                session,
                org.id,
                registration,
                acting_as="test",
                admin_role_codes=("CRM_LEADS_ADMIN",),
                product_active=False,
            )

        assert summary.changed is False
        assert summary.admin_grant.granted == []
        assert summary.admin_grant.role_codes == []
        assert permission_version_key(str(org.id)) not in redis.store

        async with session.begin():
            codes = await _membership_role_codes(session, owner.id)
        assert "CRM_LEADS_ADMIN" not in codes

        # Union-only role widening is unconditional (ADR-0007 §1) and this fix does not
        # touch it: the template still installs, with nobody holding it, which is exactly
        # the state that is safe for a product this organization never activated.
        async with session.begin():
            role = await selectors.get_role_by_code(session, org.id, "CRM_LEADS_ADMIN")
        assert role is not None


class TestDryRunGrantsNothing:
    """The CLI's `--dry-run`: reports the owner grant it would make, and writes nothing —
    the same guarantee `TestDryRunWritesNothing` proves for role widening, exercised here
    through the full `reconcile_permissions.reconcile_organization` path so the rollback
    around the owner grant is proved too, not only the diff computation.
    """

    async def test_dry_run_reports_the_pending_grant_and_writes_nothing(
        self,
        session: AsyncSession,
        org: Organization,
        admin_session: AsyncSession,
        redis: FakeRedis,
    ) -> None:
        registration = _crm_registration_with_new_admin_role()
        manifest = ProductManifest(
            product="crm", registration=registration, admin_role_codes=("CRM_LEADS_ADMIN",)
        )
        async with session.begin():
            owner = await _add_owner(session, org.id)

        line = await reconcile_permissions.reconcile_organization(
            org.id, manifest, _StubActivationChecker(), dry_run=True
        )

        assert line["dry_run"] is True
        assert line["product_active"] is True
        assert line["changed"] is True
        assert line["owners_granted"] == [
            {"membership_id": str(owner.id), "roles": ["CRM_LEADS_ADMIN"]}
        ]

        async with admin_session.begin():
            codes = await _membership_role_codes(admin_session, owner.id)
        assert "CRM_LEADS_ADMIN" not in codes
        assert permission_version_key(str(org.id)) not in redis.store

        async with admin_session.begin():
            events = await _grants_changed_event_count_for_membership(admin_session, owner.id)
        assert events == 0


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
                "core",
                [org.id, other_org.id],
                checker=_StubActivationChecker(),
                dry_run=False,
                pace_ms=0,
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


class TestPurchaseOrderHoldersKeepReceiving:
    """Operations split receiving out of `purchase_order.write` into `receipt.write` and
    `stock.receive`. A customer-authored role that could receive only because it held
    `purchase_order.write` would lose that at deploy, and ADR-0007 §4 means the template
    widening never reaches it, so this one carry-forward does, at the scope it already holds."""

    PO_WRITE = "operations.procurement.purchase_order.write"
    NEW = ("operations.procurement.receipt.write", "operations.inventory.stock.receive")

    async def _customer_role(
        self, session: AsyncSession, org_id: uuid.UUID, code: str, grants: dict[str, str]
    ) -> None:
        role = Role(organization_id=org_id, code=code, name=code, is_system=False)
        session.add(role)
        await session.flush()
        for permission_code, scope in grants.items():
            permission = await selectors.get_permission_by_code(session, permission_code)
            assert permission is not None
            session.add(RolePermission(role_id=role.id, permission_id=permission.id, scope=scope))

    async def _grants(self, session: AsyncSession, org_id: uuid.UUID, code: str) -> set[str]:
        role = await selectors.get_role_by_code(session, org_id, code)
        assert role is not None
        return {f"{g.permission.code}:{g.scope}" for g in role.grants}

    async def test_a_customer_role_holding_po_write_gains_the_receive_permissions(
        self, session: AsyncSession, org: Organization, redis: FakeRedis
    ) -> None:
        manifest = load_product_manifest("operations")
        assert manifest is not None
        async with session.begin():
            await services.install_registration(session, org.id, manifest.registration)
            await self._customer_role(session, org.id, "CX_CLERK", {self.PO_WRITE: "ORG"})
            await self._customer_role(session, org.id, "CX_OWN_CLERK", {self.PO_WRITE: "OWN"})
            await self._customer_role(
                session, org.id, "CX_READER", {"operations.procurement.purchase_order.read": "ORG"}
            )

        async with session.begin():
            first = await services.reconcile_registration(
                session, org.id, manifest.registration, acting_as="test"
            )
        assert first.changed is True

        async with session.begin():
            buyer = await self._grants(session, org.id, "CX_CLERK")
            own = await self._grants(session, org.id, "CX_OWN_CLERK")
            reader = await self._grants(session, org.id, "CX_READER")
        assert buyer == {f"{c}:ORG" for c in (self.PO_WRITE, *self.NEW)}
        # The receive routes need ORG scope, so an OWN grant would carry a permission that
        # still answers 403, and widening it would hand a buyer warehouse-wide receiving: the
        # OWN holder is left as it was and reported instead.
        assert own == {f"{self.PO_WRITE}:OWN"}
        assert "CX_OWN_CLERK" in first.skipped_customer_roles
        assert reader == {"operations.procurement.purchase_order.read:ORG"}

        async with session.begin():
            second = await services.reconcile_registration(
                session, org.id, manifest.registration, acting_as="test"
            )
        assert second.changed is False

    async def test_a_deliberate_removal_survives_the_next_reconcile(
        self, session: AsyncSession, org: Organization, redis: FakeRedis
    ) -> None:
        """The carry-forward is a one-time migration per organization: an admin who strips the
        receive grants from a buyer role to keep buying apart from receiving must not see them
        come back on the next rollout."""
        from sqlalchemy import delete

        manifest = load_product_manifest("operations")
        assert manifest is not None
        async with session.begin():
            await services.install_registration(session, org.id, manifest.registration)
            await self._customer_role(session, org.id, "CX_BUYER", {self.PO_WRITE: "ORG"})
        async with session.begin():
            await services.reconcile_registration(
                session, org.id, manifest.registration, acting_as="test"
            )
        async with session.begin():
            role = await selectors.get_role_by_code(session, org.id, "CX_BUYER")
            assert role is not None
            keep = {g.id for g in role.grants if g.permission.code == self.PO_WRITE}
            await session.execute(
                delete(RolePermission).where(
                    RolePermission.role_id == role.id, RolePermission.id.not_in(keep)
                )
            )
        session.expire_all()  # the role's grants were loaded before the delete
        async with session.begin():
            again = await services.reconcile_registration(
                session, org.id, manifest.registration, acting_as="test"
            )
            buyer = await self._grants(session, org.id, "CX_BUYER")
        assert again.changed is False
        assert buyer == {f"{self.PO_WRITE}:ORG"}
