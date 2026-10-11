"""Sign-up: a stranger creates an organisation and becomes its owner (ADR-0013 §3).

Driven the way production drives it, for the reason `test_invitation_accept.py` gives: the route
opens its own tenant-scoped transaction through `session_scope`, on the app role, bound to an id
it generated before the organisation existed. Overriding that would test around the one thing
worth proving — that the ordinary policy admits exactly the new organisation's rows and nothing
else.

Four properties carry the file:

* the organisation, its core roles, the user and an `is_owner` membership holding `ORG_OWNER`
  all land, under the app role, with no bypass;
* no credential is created — auth-service's schema is not this service's to write;
* a taken slug and a known email are each a 409 with a plain sentence;
* the writes are bound to the new organisation and only it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.api import routes
from app.config import settings
from app.models import AuditLog, Membership, Organization, OutboxEvent, Role
from app.schemas import SignupIn
from platform_core.db import dispose_engine, init_engine
from platform_core.errors import ConflictProblem

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def app_engine() -> AsyncIterator[None]:
    """The process-wide app engine `session_scope` opens — the real write path, not a stub."""
    init_engine(settings())
    try:
        yield
    finally:
        await dispose_engine()


def _request() -> Request:
    scope = {"type": "http", "method": "POST", "path": "/api/v1/account/signup", "headers": []}
    return Request(scope)


def _payload(**overrides: object) -> SignupIn:
    run = uuid.uuid4().hex[:8]
    base: dict[str, object] = {
        "organization_name": "Greenleaf Organics",
        "slug": f"greenleaf-{run}",
        "full_name": "Asha Verma",
        "email": f"asha-{run}@example.com",
    }
    base.update(overrides)
    return SignupIn(**base)  # type: ignore[arg-type]


class TestAStrangerSignsUp:
    async def test_the_organisation_and_its_owner_exist_and_nothing_else_does(
        self, app_engine: None, admin_session: AsyncSession, directory_session: AsyncSession
    ) -> None:
        payload = _payload()

        out = await routes.signup(payload, directory_session, _request())

        assert out.slug == payload.slug
        assert out.email == payload.email
        assert "inbox" in out.next_step

        # Read back on a clean BYPASSRLS session: what committed, not what the writer still held.
        async with admin_session.begin():
            org = await admin_session.get(Organization, out.organization_id)
            assert org is not None, "the organisation was not written"
            assert org.slug == payload.slug and org.status == "trial"
            assert org.trial_ends_at is not None

            user = await selectors.get_user_by_email(admin_session, payload.email)
            assert user is not None, "the owner's user was not created"

            membership = (
                await admin_session.execute(
                    select(Membership).where(
                        Membership.organization_id == org.id, Membership.user_id == user.id
                    )
                )
            ).scalar_one()
            assert membership.is_owner is True, "holding ORG_OWNER is not being the owner"
            assert membership.status == "active"
            await admin_session.refresh(membership, ["roles"])
            role_ids = {mr.role_id for mr in membership.roles}
            owner = await selectors.get_role_by_code(admin_session, org.id, "ORG_OWNER")
            assert owner is not None and owner.id in role_ids

            # The core registration landed: the organisation has roles to hold at all.
            roles = (
                (await admin_session.execute(select(Role).where(Role.organization_id == org.id)))
                .scalars()
                .all()
            )
            assert {r.code for r in roles} >= {"ORG_OWNER", "ORG_ADMIN"}

            audit = (
                await admin_session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org.id, AuditLog.action == "org.signed_up"
                    )
                )
            ).scalar_one_or_none()
            assert audit is not None, "sign-up is not audited"

            # Events for the consumers that make the organisation real elsewhere.
            staged = (
                (
                    await admin_session.execute(
                        select(OutboxEvent.event_type).where(OutboxEvent.organization_id == org.id)
                    )
                )
                .scalars()
                .all()
            )
            assert "core.org.created" in staged or any(e.endswith("org.created") for e in staged)
            assert any(e.endswith("user.created") for e in staged)

    async def test_the_payload_carries_no_password_field(self) -> None:
        """The auth schema is another service's. Sign-up cannot accept a password because the
        contract has nowhere to put one; the owner sets it by link once the inbox has proved
        itself. Pinned so a convenience field never grows here."""
        assert "password" not in SignupIn.model_fields
        assert not any("password" in name for name in SignupIn.model_fields)


class TestTheTwoRefusals:
    async def test_a_taken_slug_is_a_409(
        self, app_engine: None, directory_session: AsyncSession
    ) -> None:
        first = _payload()
        await routes.signup(first, directory_session, _request())

        with pytest.raises(ConflictProblem, match="already taken"):
            await routes.signup(_payload(slug=first.slug), directory_session, _request())

    async def test_a_known_email_is_a_409_that_says_sign_in(
        self, app_engine: None, directory_session: AsyncSession
    ) -> None:
        """An existing account does not get a second organisation from the sign-up form."""
        first = _payload()
        await routes.signup(first, directory_session, _request())

        with pytest.raises(ConflictProblem, match="Sign in"):
            await routes.signup(_payload(email=first.email), directory_session, _request())

    async def test_a_refusal_writes_nothing(
        self, app_engine: None, admin_session: AsyncSession, directory_session: AsyncSession
    ) -> None:
        first = _payload()
        await routes.signup(first, directory_session, _request())
        clash = _payload(slug=first.slug)
        with pytest.raises(ConflictProblem):
            await routes.signup(clash, directory_session, _request())
        async with admin_session.begin():
            assert await selectors.get_user_by_email(admin_session, clash.email) is None


class TestTheWritesAreBoundToTheNewOrganisation:
    async def test_two_signups_cannot_see_each_other(
        self, app_engine: None, admin_session: AsyncSession, directory_session: AsyncSession
    ) -> None:
        """Each sign-up runs under its own id on the app role. The second must neither see
        nor touch the first: same policy as any two customers."""
        a = await routes.signup(_payload(), directory_session, _request())
        b = await routes.signup(_payload(), directory_session, _request())
        assert a.organization_id != b.organization_id
        async with admin_session.begin():
            for out in (a, b):
                members = (
                    (
                        await admin_session.execute(
                            select(Membership).where(
                                Membership.organization_id == out.organization_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(members) == 1, "a sign-up wrote a membership into another organisation"


class TestOneMailboxGetsOneSignup:
    """Sign-up itself sends no mail; the dashboard follows it with auth's
    `/auth/password/forgot`, which caps mail per user (`PASSWORD_RESETS_PER_HOUR`). What keeps
    sign-up from minting a fresh user — and so a fresh reset allowance — for the same inbox is
    the case-insensitive email lookup (`users.email` is CITEXT). Pinned, because a plain `text`
    column would turn every case variant of one address into a new account."""

    async def test_a_case_variant_of_a_known_email_is_the_same_409_and_writes_nothing(
        self, app_engine: None, admin_session: AsyncSession, directory_session: AsyncSession
    ) -> None:
        first = _payload()
        await routes.signup(first, directory_session, _request())

        local, domain = first.email.split("@")
        variant = _payload(email=f"{local.upper()}@{domain}")
        with pytest.raises(ConflictProblem, match="Sign in"):
            await routes.signup(variant, directory_session, _request())

        async with admin_session.begin():
            assert not await selectors.slug_taken(admin_session, variant.slug)
