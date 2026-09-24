"""Console's narrow bespoke-pricing endpoint — `/admin/internal/catalog/...`.

Three properties: it needs *both* the console service token and a genuine `owner` operator
token (either missing refuses the call); it refuses an unknown product with 404 and a product
with no public flat plan with 409; and a repeated call with the same organization/product/price
reuses the version it already made rather than minting a second one.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
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

    async def test_a_product_audience_token_is_refused_as_not_found(self, client) -> None:
        """The audience check is `require_operator`'s own: a product token gets the same 404 an
        unknown route would, never a 403 that confirms the surface exists."""
        with as_principal(operator_principal(staff=False)):
            resp = await client.post(
                _url(uuid.uuid4()),
                json={"amount_minor": 50000, "currency": "INR", "billing_interval": "month"},
                headers={"X-Internal-Token": _console_token()},
            )
        assert resp.status_code == 404

    async def test_a_non_owner_operator_role_is_refused(self, client) -> None:
        with as_principal(operator_principal(roles=["billing_ops"])):
            resp = await client.post(
                _url(uuid.uuid4()),
                json={"amount_minor": 50000, "currency": "INR", "billing_interval": "month"},
                headers={"X-Internal-Token": _console_token()},
            )
        assert resp.status_code == 403


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
