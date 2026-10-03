"""The public invitation-accept path, driven the way production drives it.

`POST /api/v1/invitations/accept` is the one write route with no principal: the invitee holds a
token, not an account. That is exactly why it is easy to test wrongly. An earlier version of the
route depended on the tenant chokepoint, which refuses a caller with no principal *before the
body runs* — so over HTTP the invitee got a 401 and could never accept. No suite caught it,
because the tests that drove acceptance overrode the session dependency and reached
`accept_invitation` around the very chokepoint that broke it.

So this suite never overrides the chokepoint. It builds a real invitation on a tenant session,
then accepts it the way the route does: resolve the organization on a real **directory** session
(BYPASSRLS, SELECT-only) and let the route open its own tenant-scoped transaction for the writes.
That is bypass-then-narrow (ADR-0002, path 4), and testing it any other way is what let the
defect through.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.api import routes
from app.config import settings
from app.models import Invitation, Membership, Organization
from app.schemas import InvitationAccept, InvitationIn
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from platform_core.errors import NotFoundProblem, ProblemError
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

#: The single opaque reply every acceptance failure returns. Unknown, expired and revoked are
#: deliberately indistinguishable — a distinct message would confirm to a guesser that a token
#: was real.
OPAQUE = "That invitation is not valid"


@pytest_asyncio.fixture
async def app_engine() -> AsyncIterator[None]:
    """Initialise the process-wide application engine the route's `session_scope` opens.

    `accept_invitation` does not depend on `get_session`: it opens its own tenant-scoped
    transaction through `session_scope`, exactly as in production, and that reaches for the
    global engine. The chokepoint tests and the HTTP client fixture build their own engines and
    never touch it, so it is initialised here — which is what lets the real write path run
    rather than be stubbed.
    """
    init_engine(settings())
    try:
        yield
    finally:
        await dispose_engine()


async def _invite(
    session: AsyncSession,
    org: Organization,
    inviter: Membership,
    *,
    email: str = "invitee@example.com",
    role_codes: Sequence[str] = ("MEMBER",),
) -> tuple[Invitation, str]:
    """Create a pending invitation on a tenant session and return it with its raw token."""
    ctx = ctx_for(org.id, inviter, "core.identity.membership.write", Scope.ORG)
    async with session.begin():
        invitation, raw = await services.invite(
            session, ctx, InvitationIn(email=email, role_codes=list(role_codes))
        )
    return invitation, raw


class TestAValidInvitationIsAccepted:
    async def test_the_member_and_user_exist_and_the_invitation_is_marked_accepted(
        self,
        app_engine: None,
        session: AsyncSession,
        admin_session: AsyncSession,
        directory_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        invitation, raw = await _invite(session, org, people["manager"])

        member = await routes.accept_invitation(
            InvitationAccept(token=raw, full_name="Ivy Invitee"), directory_session
        )

        assert member.email == "invitee@example.com"
        assert member.full_name == "Ivy Invitee"
        assert "MEMBER" in member.roles

        # Read everything back on a clean BYPASSRLS session, so the assertions see what actually
        # committed rather than anything still resident in the writing session's identity map.
        async with admin_session.begin():
            landed = await selectors.get_member(admin_session, org.id, member.id)
            assert landed is not None, "the membership was not written"
            user = await selectors.get_user_by_email(admin_session, "invitee@example.com")
            assert user is not None, "the user was not created"

            refreshed = await admin_session.get(Invitation, invitation.id)
            assert refreshed is not None
            assert refreshed.accepted_at is not None, "the invitation was not marked accepted"
            assert refreshed.accepted_user_id == member.user_id

    async def test_the_writes_land_in_the_invitations_org_and_only_there(
        self,
        app_engine: None,
        session: AsyncSession,
        admin_session: AsyncSession,
        directory_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """The cross-tenant safety claim: the narrow half writes under the org the bypass found.

        The resolver runs BYPASSRLS, but every write happens inside a transaction bound to the
        organization it returned — so the new membership belongs to that org, and to no other.
        """
        invitation, raw = await _invite(session, org, people["manager"])

        member = await routes.accept_invitation(
            InvitationAccept(token=raw, full_name="Ivy Invitee"), directory_session
        )

        landed = await selectors.get_member(admin_session, org.id, member.id)
        assert landed is not None
        assert landed[0].organization_id == invitation.organization_id == org.id

        # Across every organization — admin_session bypasses row-level security — this person
        # holds exactly one membership, in the invitation's org. Nothing leaked into another.
        rows = (
            (
                await admin_session.execute(
                    select(Membership).where(Membership.user_id == member.user_id)
                )
            )
            .scalars()
            .all()
        )
        assert [m.organization_id for m in rows] == [org.id]


class TestEveryFailureIsTheSameOpaqueReply:
    async def test_an_unknown_token_is_a_404_with_the_opaque_message(
        self,
        app_engine: None,
        directory_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        with pytest.raises(ProblemError) as caught:
            await routes.accept_invitation(
                InvitationAccept(token="z" * 40, full_name="Nobody"), directory_session
            )
        assert caught.value.status_code == 404
        assert caught.value.detail == OPAQUE

    async def test_unknown_expired_and_revoked_are_indistinguishable(
        self,
        app_engine: None,
        session: AsyncSession,
        admin_session: AsyncSession,
        directory_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """The property that stops token enumeration, asserted across all three failure modes.

        Unknown fails in the resolver, before any tenant is bound. Expired and revoked resolve
        an organization successfully — the resolver never inspects validity — and then fail
        inside the tenant transaction. Different code paths; one identical reply.
        """
        _, expired_raw = await _invite(session, org, people["manager"], email="expired@example.com")
        _, revoked_raw = await _invite(session, org, people["manager"], email="revoked@example.com")

        async with admin_session.begin():
            expired = (
                await admin_session.execute(
                    select(Invitation).where(Invitation.email == "expired@example.com")
                )
            ).scalar_one()
            expired.expires_at = datetime.now(UTC) - timedelta(days=1)
            revoked = (
                await admin_session.execute(
                    select(Invitation).where(Invitation.email == "revoked@example.com")
                )
            ).scalar_one()
            revoked.revoked_at = datetime.now(UTC)

        replies: list[tuple[int, str]] = []
        for token in ("z" * 40, expired_raw, revoked_raw):
            with pytest.raises(ProblemError) as caught:
                await routes.accept_invitation(
                    InvitationAccept(token=token, full_name="Nobody"), directory_session
                )
            replies.append((caught.value.status_code, caught.value.detail))

        assert replies == [(404, OPAQUE)] * 3, (
            "unknown, expired and revoked must be indistinguishable, or a guesser learns from "
            f"the reply that a token was real: {replies}"
        )


class TestTheEmailMatchGuard:
    """`expected_email` (keyword-only, defaults to `None`) is what the forthcoming
    `accept-federated` internal route uses: a Google/Microsoft login proves an email, not a
    token holder's identity beyond that, and provisioning has always been keyed by
    `invitation.email` (`add_member` below). Without this check, holding an invitation link
    would let anyone accept it under their own OAuth identity — see
    docs/superpowers/specs/2026-10-03-oauth2-social-login-design.md §6.

    Written here, against the service function directly, rather than against the route: the
    guard is a property of `accept_invitation` itself, and the public route must keep passing
    no `expected_email` at all and stay provably unaffected (the third test below).
    """

    async def test_a_mismatched_email_is_refused_with_the_same_opaque_message(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """Same `NotFoundProblem` and the same sentence as an unknown, expired or revoked
        token — a forged attempt that got the token right and the email wrong must read
        exactly like one that got nothing right at all, or the reply itself tells an attacker
        they are close."""
        _, raw = await _invite(session, org, people["manager"])

        async with session.begin():
            with pytest.raises(NotFoundProblem) as caught:
                await services.accept_invitation(
                    session, raw, "Someone Else", expected_email="attacker@evil.com"
                )
        assert caught.value.status_code == 404
        assert caught.value.detail == OPAQUE

    async def test_a_matching_email_is_accepted_case_insensitively(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """The invitation's own address folds to lower case for the comparison — an invitee
        whose OAuth profile reports `Invitee@Example.com` must not be refused on casing
        alone."""
        _, raw = await _invite(session, org, people["manager"])

        async with session.begin():
            membership = await services.accept_invitation(
                session, raw, "Invitee Name", expected_email="INVITEE@EXAMPLE.COM"
            )
        assert membership is not None

    async def test_no_expected_email_still_works_unaffected(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """The existing public route never passes `expected_email` — this is the regression
        guard proving the new keyword-only parameter is purely additive."""
        _, raw = await _invite(session, org, people["manager"])

        async with session.begin():
            membership = await services.accept_invitation(session, raw, "Invitee Name")
        assert membership is not None


class TestThePublicPathNeedsNoToken:
    async def test_an_anonymous_http_request_is_accepted_not_401(
        self,
        app_engine: None,
        session: AsyncSession,
        client: AsyncClient,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """The regression the chokepoint introduced: no bearer token, and no 401.

        The route carries no principal dependency, so a truly anonymous request reaches the
        body. Before bypass-then-narrow it never did — the tenant chokepoint refused it first.
        The client fixture sets no principal; the request succeeds because none is required and
        none would help.
        """
        _, raw = await _invite(session, org, people["manager"])

        response = await client.post(
            "/api/v1/invitations/accept",
            json={"token": raw, "full_name": "Ivy Invitee"},
        )

        assert response.status_code != 401, response.text
        assert response.status_code in (200, 201), response.text
        assert response.json()["email"] == "invitee@example.com"


def test_the_accept_route_takes_no_principal() -> None:
    """A static guard: the moment someone gives this route a principal, it stops being public.

    Reaching for `SessionDep` or a `ScopeContext` here would route it back through the tenant
    chokepoint and reintroduce the 401 for the one caller who legitimately has no account.
    """
    annotations = {
        name: str(param.annotation)
        for name, param in inspect.signature(routes.accept_invitation).parameters.items()
    }
    assert "DirectorySessionDep" in annotations.get("directory", ""), annotations
    assert "SessionDep" not in annotations.values()
    assert not any("ScopeContext" in ann or "Principal" in ann for ann in annotations.values())
