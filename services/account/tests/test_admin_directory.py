"""The service-to-service-reachable route console's org-detail composite calls:
`GET /admin/directory/organizations/{id}`.

The one read into account-service's directory data console is allowed to make for this —
never `/internal/v1`, which authorizes no individual caller at all (ADR-0003 §2,
`app/api/admin.py`'s module docstring). Two credentials, both required: console's service token
(`X-Internal-Token`, `iss="console"`, `aud="account"`) says *who is calling* (m6 of the
third-round security review), and — since MINOR 7 of the 2026-09-24 review — the operator's own
bearer access token, forwarded verbatim in `X-Operator-Token`, says *on whose behalf*; this route
verifies it itself via JWKS. `decode_token` is stubbed here to a plain claims lookup, the same
shape `test_gateway_body_limit.py` uses for the identical reason: exercising the *verification
logic* (audience, amr, role, sub) without standing up a real signing key and JWKS endpoint.

Properties:

0. **Either credential missing is refused** — an operator token with no service token, and a
   service token with no operator token, are both 401; so is a service token from any caller
   other than console, or addressed to any other service.
1. **A missing token is refused** — 401, before any lookup.
2. **A wrong-audience token is refused** — a product-audience token proves nothing about being
   staff.
3. **`otp` must be in `amr`** — an operator token minted before MFA completed is refused even
   though it verifies.
4. **A role outside the read set is refused.**
5. **A bad or non-UUID `sub` is refused** — 401, not a 500.
6. **The read is audited**, bound to the target organization, attributed to the token's own
   `sub` — never a value console merely claimed.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import app.api.admin as admin_module
from app.config import settings
from app.models import AuditLog, Organization
from platform_core.errors import UnauthorizedProblem
from platform_core.internal_auth import mint_service_token

pytestmark = pytest.mark.integration


def _token(
    *,
    sub: str | None = None,
    aud: str = "operator",
    roles: list[str] | None = None,
    amr: list[str] | None = None,
) -> str:
    """An opaque stand-in for a signed operator access token — `_fake_decode_token` below is
    the only thing that ever reads it, so its shape only has to match what that stub expects."""
    return json.dumps(
        {
            "sub": sub if sub is not None else str(uuid.uuid4()),
            "aud": aud,
            "roles": roles if roles is not None else ["owner"],
            "amr": amr if amr is not None else ["pwd", "otp"],
        }
    )


def _fake_decode_token(
    _settings: object, token: str, *, expected_type: str, audience: str | None = None
) -> dict[str, Any]:
    """Stands in for `platform_core.auth.decode_token`'s JWKS verification — parses the JSON
    `_token` produced and enforces the one thing the real function enforces that this route's
    own logic depends on: the audience."""
    try:
        claims: dict[str, Any] = json.loads(token)
    except (TypeError, ValueError) as exc:
        raise UnauthorizedProblem("Token is not valid") from exc
    if audience is not None and claims.get("aud") != audience:
        raise UnauthorizedProblem("Token is not valid")
    return claims


@pytest.fixture(autouse=True)
def _stub_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin_module, "decode_token", _fake_decode_token)


def _service_token(*, caller: str = "console", audience: str = "account") -> str:
    return mint_service_token(settings(), audience=audience, caller=caller)


def _headers(token: str, *, service: bool = True) -> dict[str, str]:
    """The operator token, plus — by default — console's service token, the way console sends
    both. `service=False` is the operator-token-only shape m6 refuses."""
    headers = {"X-Operator-Token": token}
    if service:
        headers["X-Internal-Token"] = _service_token()
    return headers


class TestBothCredentialsAreRequired:
    """m6: the operator's access token alone must not open a cross-tenant read, and console's
    service token alone must not either."""

    async def test_an_operator_token_without_a_service_token_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers(_token(), service=False),
        )
        assert response.status_code == 401

    async def test_a_service_token_without_an_operator_token_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers={"X-Internal-Token": _service_token()},
        )
        assert response.status_code == 401

    async def test_a_service_token_from_a_caller_other_than_console_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers={
                "X-Operator-Token": _token(),
                "X-Internal-Token": _service_token(caller="billing"),
            },
        )
        assert response.status_code == 401

    async def test_a_service_token_addressed_to_another_service_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers={
                "X-Operator-Token": _token(),
                "X-Internal-Token": _service_token(audience="billing"),
            },
        )
        assert response.status_code == 401


class TestAdminDirectoryRoute:
    async def test_console_reads_the_organizations_directory_record(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers(_token(roles=["owner"], amr=["pwd", "otp"])),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == str(org.id)
        assert body["slug"] == org.slug

    async def test_no_token_at_all_is_refused(self, client: AsyncClient, org: Organization) -> None:
        response = await client.get(f"/api/v1/admin/directory/organizations/{org.id}")
        assert response.status_code == 401

    async def test_a_malformed_token_is_refused_not_a_500(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers("not-even-json"),
        )
        assert response.status_code == 401

    async def test_a_product_audience_token_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        """A tenant's own token proves nothing about being staff — MINOR 7's whole point is
        that this route no longer takes anyone's word for the audience either."""
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers(_token(aud="app", roles=["owner"], amr=["pwd", "otp"])),
        )
        assert response.status_code == 401

    async def test_a_bad_or_non_uuid_sub_is_refused_with_401_not_500(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers(_token(sub="not-a-uuid", roles=["owner"], amr=["pwd", "otp"])),
        )
        assert response.status_code == 401

    async def test_a_role_outside_the_read_set_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers(_token(roles=[], amr=["pwd", "otp"])),
        )
        assert response.status_code == 403

    async def test_a_token_with_no_completed_second_factor_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        """Even a verified, correctly-audienced token is refused if it never proved a second
        factor — the same `otp`-in-`amr` check `require_operator` runs everywhere else."""
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers(_token(roles=["owner"], amr=["pwd"])),
        )
        assert response.status_code == 403

    async def test_every_operator_role_may_read(
        self, client: AsyncClient, org: Organization
    ) -> None:
        for roles in (["owner"], ["billing_ops"], ["support"]):
            response = await client.get(
                f"/api/v1/admin/directory/organizations/{org.id}",
                headers=_headers(_token(roles=roles, amr=["pwd", "otp"])),
            )
            assert response.status_code == 200, roles

    async def test_an_unknown_organization_is_404(self, client: AsyncClient) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{uuid.uuid4()}",
            headers=_headers(_token(roles=["owner"], amr=["pwd", "otp"])),
        )
        assert response.status_code == 404

    async def test_the_read_is_audited_and_attributed_to_the_tokens_own_sub(
        self, client: AsyncClient, session, org: Organization
    ) -> None:
        operator_id = uuid.uuid4()
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers(_token(sub=str(operator_id), roles=["owner"], amr=["pwd", "otp"])),
        )
        assert response.status_code == 200

        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == org.id, AuditLog.action == "organization.viewed"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].actor_id == operator_id
        assert rows[0].acting_as == f"operator:{operator_id}"
