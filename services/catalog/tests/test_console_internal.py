"""Console's narrow bespoke-pricing endpoint — `/admin/internal/catalog/...`.

Three properties: it needs *both* the console service token and a genuine operator token holding
`owner` or `billing_ops` (either missing refuses the call); it refuses an unknown product with 404
and a product with no public flat plan with 409; and a repeated call with the same
organization/product/price reuses the version it already made rather than minting a second one.

Fix round 1 (independent security review of the admin-client-pricing slice) added: `product_key`
path-injection rejection (MAJOR-1), `billing_ops` admitted end to end with a genuinely signed
token rather than the `as_principal` override (MAJOR-2), and a race on `uq_plan_versions_
plan_version` recovering idempotently instead of a bare 500 or a spurious "conflicts with
something" (the concurrency finding).
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.config import settings
from app.main import app
from app.models import PlanVersion
from app.schemas import FeatureIn, PlanCreateIn, PriceIn, VersionCreateIn
from platform_core.auth import Principal, get_principal
from platform_core.internal_auth import mint_service_token
from tests.helpers import operator_principal

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _console_token() -> str:
    return mint_service_token(settings(), audience="catalog", caller="console")


def _foreign_token() -> str:
    return mint_service_token(settings(), audience="catalog", caller="some-other-service")


@contextlib.contextmanager
def real_signed_operator_token(
    monkeypatch: pytest.MonkeyPatch, *, roles: list[str]
) -> Iterator[str]:
    """A genuinely signed, genuinely verified operator bearer token — not the
    `dependency_overrides[get_principal]` shortcut every other test in this module uses.

    MAJOR-2 (fix round 1) asks for an end-to-end test with a real `billing_ops` token so the role
    gate on this route (and `platform_core.auth.get_principal`'s real signature/audience/`otp`
    checks) cannot silently regress. Mirrors `services/admin-gateway/tests/conftest.py`'s own
    `mint`/JWKS-stub pattern: a real Ed25519 keypair signs the token, and `platform_core.auth`'s
    module-level JWKS cache is patched to hand back this test's own public key instead of
    fetching a real JWKS document — the cryptographic verification, the audience check and the
    role check in `require_operator` all run for real.
    """
    from platform_core import auth as platform_auth

    key = Ed25519PrivateKey.generate()
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": settings().jwt_issuer,
        "sub": str(uuid.uuid4()),
        "aud": "operator",
        "act_org": str(uuid.uuid4()),
        "iat": now - 10,
        "exp": now + 900,
        "jti": uuid.uuid4().hex,
        "typ": "access",
        "sid": str(uuid.uuid4()),
        "ent": [],
        "roles": roles,
        "loc": [],
        "amr": ["pwd", "otp"],
        "pv": 1,
    }
    token = jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "test"})

    class _Key:
        def __init__(self) -> None:
            self.key = key.public_key()

    class _JwkClient:
        def get_signing_key_from_jwt(self, _token: str) -> _Key:
            return _Key()

    monkeypatch.setattr(platform_auth, "_jwk_client", _JwkClient())
    try:
        yield token
    finally:
        monkeypatch.setattr(platform_auth, "_jwk_client", None)


async def _seed_public_flat_plan(
    session: AsyncSession, *, product_key: str = "hrms", amount_minor: int = 199900
) -> None:
    op = operator_principal()
    plan = await services.create_plan(
        session,
        op,
        PlanCreateIn(product_key=product_key, code="flat", name="Flat plan"),
        request=None,
    )
    version = await services.create_version(
        session,
        op,
        plan,
        VersionCreateIn(
            billing_interval="month",
            currency="INR",
            prices=[PriceIn(kind="flat", amount_minor=amount_minor, currency="INR")],
            features=[FeatureIn(feature_key="trial_days", value=14)],
        ),
        request=None,
    )
    await services.publish_version(session, op, version, request=None)


def _url(organization_id: uuid.UUID, product_key: str = "hrms") -> str:
    return (
        f"/admin/internal/catalog/organizations/{organization_id}"
        f"/products/{product_key}/bespoke-price"
    )


class TestBespokePriceAuthentication:
    async def test_without_a_service_token_it_is_refused(self, client) -> None:
        with as_principal(operator_principal(roles=["owner"])):
            resp = await client.post(
                _url(uuid.uuid4()),
                json={"amount_minor": 50000, "currency": "INR", "billing_interval": "month"},
            )
        assert resp.status_code == 401

    async def test_a_service_token_from_another_caller_is_refused(self, client) -> None:
        with as_principal(operator_principal(roles=["owner"])):
            resp = await client.post(
                _url(uuid.uuid4()),
                json={"amount_minor": 50000, "currency": "INR", "billing_interval": "month"},
                headers={"X-Internal-Token": _foreign_token()},
            )
        assert resp.status_code == 401

    async def test_without_an_operator_token_it_is_refused(self, client) -> None:
        resp = await client.post(
            _url(uuid.uuid4()),
            json={"amount_minor": 50000, "currency": "INR", "billing_interval": "month"},
            headers={"X-Internal-Token": _console_token()},
        )
        assert resp.status_code == 401

    async def test_a_product_audience_token_is_refused_as_not_found(
        self, client, platform_session: AsyncSession
    ) -> None:
        """The audience check is `require_operator`'s own: a product token gets the same 404 an
        unknown route would, never a 403 that confirms the surface exists.

        Fix round 1 (mutation M2b survived): a real product is seeded so this 404 can only come
        from the audience check itself — without a seeded product, `product_has_any_plan` would
        also answer 404 for an *allowed* caller, and a mutant that weakened or removed the
        audience check entirely would still pass this test for the wrong reason.
        """
        async with platform_session.begin():
            await _seed_public_flat_plan(platform_session, product_key="hrms")
        with as_principal(operator_principal(staff=False)):
            resp = await client.post(
                _url(uuid.uuid4(), product_key="hrms"),
                json={"amount_minor": 50000, "currency": "INR", "billing_interval": "month"},
                headers={"X-Internal-Token": _console_token()},
            )
        assert resp.status_code == 404

    async def test_a_role_outside_owner_and_billing_ops_is_refused(
        self, client, platform_session: AsyncSession
    ) -> None:
        """`support` holds no write role at all here (MAJOR-2 narrowed this route to `owner` and
        `billing_ops` specifically, never "any operator role") — seeded for the same M2b reason
        as the audience test above, so this 403 cannot be a 404 in disguise."""
        async with platform_session.begin():
            await _seed_public_flat_plan(platform_session, product_key="hrms")
        with as_principal(operator_principal(roles=["support"])):
            resp = await client.post(
                _url(uuid.uuid4(), product_key="hrms"),
                json={"amount_minor": 50000, "currency": "INR", "billing_interval": "month"},
                headers={"X-Internal-Token": _console_token()},
            )
        assert resp.status_code == 403

    async def test_billing_ops_may_write_with_a_real_signed_token(
        self, client, platform_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MAJOR-2 (fix round 1): a `billing_ops` operator must reach this route end to end —
        proven with a genuinely signed, genuinely verified token (`real_signed_operator_token`),
        not the `as_principal` shortcut every other test in this module uses, so a regression in
        `require_operator`'s own signature/audience/role checks cannot hide behind a test that
        never actually exercised them."""
        org = uuid.uuid4()
        async with platform_session.begin():
            await _seed_public_flat_plan(platform_session, product_key="hrms")

        with real_signed_operator_token(monkeypatch, roles=["billing_ops"]) as token:
            resp = await client.post(
                _url(org, product_key="hrms"),
                json={"amount_minor": 50000, "currency": "INR", "billing_interval": "month"},
                headers={
                    "X-Internal-Token": _console_token(),
                    "Authorization": f"Bearer {token}",
                },
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["restricted_to_org_id"] == str(org)

    async def test_a_product_key_outside_the_pattern_is_rejected(self, client) -> None:
        """MAJOR-1 (fix round 1): the path parameter has the same shape constraint the console
        request body does. This is the defence-in-depth layer for a `product_key` that reaches
        this route directly without a literal `/` in it (an httpx client — console's own
        included — normalises `..`/`%2F` path segments per RFC 3986 before a request ever leaves
        the process, which is what closes the slash-carrying variant of MAJOR-1 upstream of this
        route entirely; `tests/test_pricing.py` on the console side is what proves *that* half).
        A value the pattern refuses is a plain 422 before the route body ever runs."""
        with as_principal(operator_principal(roles=["owner"])):
            resp = await client.post(
                _url(uuid.uuid4(), product_key="Not-A-Valid-Key!"),
                json={"amount_minor": 50000, "currency": "INR", "billing_interval": "month"},
                headers={"X-Internal-Token": _console_token()},
            )
        assert resp.status_code == 422, resp.text


class TestBespokePriceAuthoring:
    async def test_unknown_product_is_404(self, client) -> None:
        with as_principal(operator_principal(roles=["owner"])):
            resp = await client.post(
                _url(uuid.uuid4(), product_key="no-such-product"),
                json={"amount_minor": 50000, "currency": "INR", "billing_interval": "month"},
                headers={"X-Internal-Token": _console_token()},
            )
        assert resp.status_code == 404

    async def test_a_product_with_no_public_flat_plan_is_409(
        self, client, platform_session: AsyncSession
    ) -> None:
        op = operator_principal()
        async with platform_session.begin():
            # A plan exists for the product, but it is never published — no public flat plan.
            await services.create_plan(
                platform_session,
                op,
                PlanCreateIn(product_key="crm", code="unpublished", name="Draft only"),
                request=None,
            )
        with as_principal(operator_principal(roles=["owner"])):
            resp = await client.post(
                _url(uuid.uuid4(), product_key="crm"),
                json={"amount_minor": 50000, "currency": "INR", "billing_interval": "month"},
                headers={"X-Internal-Token": _console_token()},
            )
        assert resp.status_code == 409

    async def test_it_clones_and_publishes_a_bespoke_version(
        self, client, platform_session: AsyncSession
    ) -> None:
        org = uuid.uuid4()
        async with platform_session.begin():
            await _seed_public_flat_plan(platform_session, product_key="hrms")

        with as_principal(operator_principal(roles=["owner"])):
            resp = await client.post(
                _url(org, product_key="hrms"),
                json={"amount_minor": 75000, "currency": "INR", "billing_interval": "month"},
                headers={"X-Internal-Token": _console_token()},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "published"
        assert body["restricted_to_org_id"] == str(org)
        assert body["prices"][0]["amount_minor"] == 75000
        assert body["prices"][0]["currency"] == "INR"
        # The public plan's trial_days feature carried over onto the bespoke version.
        feature_keys = {f["feature_key"] for f in body["features"]}
        assert "trial_days" in feature_keys

    async def test_a_repeat_request_reuses_the_same_version(
        self, client, platform_session: AsyncSession
    ) -> None:
        org = uuid.uuid4()
        async with platform_session.begin():
            await _seed_public_flat_plan(platform_session, product_key="hrms")

        body_json = {"amount_minor": 60000, "currency": "INR", "billing_interval": "month"}
        with as_principal(operator_principal(roles=["owner"])):
            first = await client.post(
                _url(org, product_key="hrms"),
                json=body_json,
                headers={"X-Internal-Token": _console_token()},
            )
            second = await client.post(
                _url(org, product_key="hrms"),
                json=body_json,
                headers={"X-Internal-Token": _console_token()},
            )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]

        count = (
            await platform_session.execute(
                select(func.count())
                .select_from(PlanVersion)
                .where(PlanVersion.restricted_to_org_id == org)
            )
        ).scalar_one()
        assert count == 1, "one bespoke version, not one per call"

    async def test_a_different_price_for_the_same_org_is_a_new_version(
        self, client, platform_session: AsyncSession
    ) -> None:
        org = uuid.uuid4()
        async with platform_session.begin():
            await _seed_public_flat_plan(platform_session, product_key="hrms")

        with as_principal(operator_principal(roles=["owner"])):
            first = await client.post(
                _url(org, product_key="hrms"),
                json={"amount_minor": 60000, "currency": "INR", "billing_interval": "month"},
                headers={"X-Internal-Token": _console_token()},
            )
            second = await client.post(
                _url(org, product_key="hrms"),
                json={"amount_minor": 65000, "currency": "INR", "billing_interval": "month"},
                headers={"X-Internal-Token": _console_token()},
            )
        assert first.json()["id"] != second.json()["id"]

        count = (
            await platform_session.execute(
                select(func.count())
                .select_from(PlanVersion)
                .where(PlanVersion.restricted_to_org_id == org)
            )
        ).scalar_one()
        assert count == 2, "immutable versions: a different price is a new row, never an edit"
