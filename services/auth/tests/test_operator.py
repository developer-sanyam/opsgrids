"""Operator (OpsGrids staff) sign-in, and the boundary that keeps it apart from tenant login.

The security property this file exists to hold: an operator token is a *different audience*
carrying `stf`, minted only when a `staff_accounts` row authenticates — and nothing on the
tenant path can produce it, while nothing on this path touches a tenant. The two identities
share a signing key and a verify path and nothing else.
"""

from __future__ import annotations

import jwt
import pytest

from app import security, services
from app.bootstrap import bootstrap_staff_owner
from app.models import RefreshToken, Session, StaffAccount
from platform_core.errors import ForbiddenProblem, UnauthorizedProblem
from tests.conftest import (
    PASSWORD,
    FakeDirectory,
    FakeEntitlements,
    user_in_one_org,
)

pytestmark = pytest.mark.integration

STAFF_EMAIL = "ops@opsgrids.tech"
STAFF_PASSWORD = "operator-correct-horse-9"  # pragma: allowlist secret


async def _make_staff(
    session,
    *,
    email: str = STAFF_EMAIL,
    password: str = STAFF_PASSWORD,
    roles: list[str] | None = None,
    status: str = "active",
) -> StaffAccount:
    staff = StaffAccount(
        email=email,
        password_hash=security.hash_password(password),
        display_name="Ops Person",
        operator_roles=roles if roles is not None else ["owner", "billing_ops"],
        status=status,
    )
    session.add(staff)
    await session.flush()
    return staff


def _verify_via_jwks(token: str, pair, cfg, *, audience: str = "operator") -> dict:
    """Decode the way a product service or gateway would — public key only, no private half."""
    return jwt.decode(
        token,
        pair.public,
        algorithms=["EdDSA"],
        audience=audience,
        issuer=cfg.jwt_issuer,
        options={"require": ["exp", "iat", "sub", "iss"]},
    )


class TestOperatorSignIn:
    async def test_a_staff_account_gets_an_operator_token_with_stf(
        self, session, cfg, pair
    ) -> None:
        """The headline: staff → a token on the operator audience carrying `stf` and roles,
        and it verifies against the public key alone (JWKS)."""
        await _make_staff(session, roles=["owner", "support"])

        issued = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        assert issued.operator_roles == ["owner", "support"]

        claims = _verify_via_jwks(issued.access_token, pair, cfg)
        assert claims["aud"] == "operator"
        assert claims["stf"] is True
        assert claims["typ"] == "access"
        assert claims["roles"] == ["owner", "support"]
        # No membership: an operator holds none, so the gateway resolves no tenant grants.
        assert "mem" not in claims
        # The structural org the gateway requires — the platform sentinel, not a tenant.
        assert claims["act_org"] == str(services.PLATFORM_ORG)

    async def test_a_wrong_password_is_a_generic_401(self, session, cfg, pair) -> None:
        await _make_staff(session)
        with pytest.raises(UnauthorizedProblem) as wrong:
            await services.operator_login(
                session,
                cfg=cfg,
                pair=pair,
                email=STAFF_EMAIL,
                password="not-the-password",  # pragma: allowlist secret
            )
        # Same message an unknown operator gives — no enumeration oracle.
        with pytest.raises(UnauthorizedProblem) as unknown:
            await services.operator_login(
                session, cfg=cfg, pair=pair, email="nobody@opsgrids.tech", password=STAFF_PASSWORD
            )
        assert str(wrong.value) == str(unknown.value) == services.INVALID

    async def test_a_disabled_operator_is_refused(self, session, cfg, pair) -> None:
        await _make_staff(session, status="disabled")
        with pytest.raises(ForbiddenProblem, match="disabled"):
            await services.operator_login(
                session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
            )

    async def test_repeated_failures_lock_the_operator(self, session, cfg, pair) -> None:
        await _make_staff(session)
        for _ in range(cfg.max_failed_attempts):
            with pytest.raises(UnauthorizedProblem):
                await services.operator_login(
                    session,
                    cfg=cfg,
                    pair=pair,
                    email=STAFF_EMAIL,
                    password="wrong",  # pragma: allowlist secret
                )
        with pytest.raises(ForbiddenProblem, match="temporarily locked"):
            await services.operator_login(
                session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
            )

    async def test_sign_in_records_a_session_and_a_refresh_token(self, session, cfg, pair) -> None:
        staff = await _make_staff(session)
        issued = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        auth_session = await session.get(Session, issued.session_id)
        assert auth_session is not None and auth_session.user_id == staff.id
        stored = await session.get(
            RefreshToken,
            jwt.decode(issued.refresh_token, options={"verify_signature": False})["jti"],
        )
        assert stored is not None and stored.user_id == staff.id


class TestTheBoundary:
    async def test_tenant_login_is_unchanged_and_carries_no_stf(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """The regression guard: a tenant token keeps its product audience and never gains
        an `stf` claim from this change."""
        outcome = await services.login(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            email="sam@example.com",
            password=PASSWORD,
        )
        assert outcome.issued is not None
        claims = jwt.decode(
            outcome.issued.access_token,
            pair.public,
            algorithms=["EdDSA"],
            audience="app",
            issuer=cfg.jwt_issuer,
        )
        assert claims["aud"] == "app"
        assert "stf" not in claims, "a tenant token must never carry the staff claim"

    async def test_a_tenant_user_cannot_obtain_an_operator_token(
        self, session, cfg, pair, registered
    ) -> None:
        """The tenant's email lives in the directory, not in `staff_accounts`; the operator
        path resolves nothing for it. No crossover from tenant to staff."""
        with pytest.raises(UnauthorizedProblem):
            await services.operator_login(
                session, cfg=cfg, pair=pair, email="sam@example.com", password=PASSWORD
            )

    async def test_a_tenant_refresh_token_is_not_valid_on_the_operator_path(
        self, session, cfg, pair, directory, registered
    ) -> None:
        tenant = await services.login(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            email="sam@example.com",
            password=PASSWORD,
        )
        assert tenant.issued is not None
        with pytest.raises(UnauthorizedProblem):
            await services.operator_rotate_refresh(
                session, cfg=cfg, pair=pair, raw_token=tenant.issued.refresh_token
            )

    async def test_an_operator_refresh_token_is_not_valid_on_the_tenant_path(
        self, session, cfg, pair
    ) -> None:
        await _make_staff(session)
        issued = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        # The tenant refresh path resolves the subject through the directory; a staff id is
        # not there, so the token is refused rather than honoured across the boundary.
        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                cfg=cfg,
                pair=pair,
                directory=FakeDirectory([user_in_one_org()]),
                entitlements=FakeEntitlements(),
                raw_token=issued.refresh_token,
            )


class TestOperatorRefreshRotation:
    async def test_refreshing_returns_a_new_operator_token(self, session, cfg, pair) -> None:
        await _make_staff(session, roles=["support"])
        issued = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        rotated = await services.operator_rotate_refresh(
            session, cfg=cfg, pair=pair, raw_token=issued.refresh_token
        )
        assert rotated.refresh_token != issued.refresh_token
        assert rotated.session_id == issued.session_id
        claims = _verify_via_jwks(rotated.access_token, pair, cfg)
        assert claims["stf"] is True and claims["roles"] == ["support"]

    async def test_replaying_a_spent_operator_refresh_kills_the_family(
        self, session, cfg, pair
    ) -> None:
        await _make_staff(session)
        issued = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        captured = issued.refresh_token
        fresh = await services.operator_rotate_refresh(
            session, cfg=cfg, pair=pair, raw_token=captured
        )
        with pytest.raises(UnauthorizedProblem, match="security reasons"):
            await services.operator_rotate_refresh(session, cfg=cfg, pair=pair, raw_token=captured)
        # The legitimate holder's newer token is dead too — the whole family is revoked.
        with pytest.raises(UnauthorizedProblem):
            await services.operator_rotate_refresh(
                session, cfg=cfg, pair=pair, raw_token=fresh.refresh_token
            )


class TestBootstrap:
    async def test_seeding_the_owner_is_idempotent(self, session) -> None:
        first = await bootstrap_staff_owner(
            session,
            email="owner@opsgrids.local",
            password="dev-operator-not-a-real-password",  # pragma: allowlist secret
            display_name="OpsGrids Owner (dev)",
            roles=["owner"],
        )
        again = await bootstrap_staff_owner(
            session,
            email="owner@opsgrids.local",
            password="a-different-one",  # pragma: allowlist secret
            display_name="Someone Else",
            roles=["support"],
        )
        assert again.id == first.id, "re-seeding must not duplicate or rewrite the owner"
        assert again.display_name == "OpsGrids Owner (dev)"
