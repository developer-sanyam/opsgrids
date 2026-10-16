"""The provider list is stated once and checked everywhere (integrations framework).

`MerchantAccount`'s provider CHECK and its webhook-secret CHECKs are built from two tuples in
`app.models`; these tests pin those tuples to the registry and the collections specs so a provider
added to one place and not the others fails here, and exercise the non-secret `config` through the
real routes: stored, returned, audited, bounded, and refused for unknown fields or the wrong role.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.collections import domain
from app.models import MERCHANT_PROVIDERS, MERCHANT_WEBHOOK_SECRET_PROVIDERS
from app.providers import registry
from tests.collections_support import ACCOUNT, READ, WRITE, as_principal, principal_for
from tests.helpers import audit_rows, rows

pytestmark = pytest.mark.integration

APP_ID = "TESTdevonlymerch01"  # pragma: allowlist secret
SECRET = "dev-only-cashfree-merchant-secret-0001"  # pragma: allowlist secret


def test_the_provider_lists_agree() -> None:
    assert (
        set(MERCHANT_PROVIDERS) == set(registry.COLLECTIONS_PROVIDER_NAMES) == set(domain.PROVIDERS)
    )
    assert set(MERCHANT_WEBHOOK_SECRET_PROVIDERS) == {
        name for name in domain.PROVIDERS if domain.spec_for(name).has_webhook_secret
    }


async def _put(client: AsyncClient, org: uuid.UUID, **over: object):  # type: ignore[no-untyped-def]
    body = {"provider": "cashfree", "key_id": APP_ID, "key_secret": SECRET, "mode": "sandbox"}
    with as_principal(principal_for(org, [READ, WRITE])):
        return await client.put(ACCOUNT, json={**body, **over})


async def test_config_is_stored_returned_and_audited_without_a_secret(
    client: AsyncClient, admin_session: AsyncSession
) -> None:
    org = uuid.uuid4()
    response = await _put(client, org, config={"merchant_id": "M-42", "salt_index": 1})
    assert response.status_code == 200, response.text
    assert response.json()["config"] == {"merchant_id": "M-42", "salt_index": 1}

    (stored,) = await rows(
        admin_session,
        "SELECT config FROM payment.merchant_accounts WHERE organization_id = :o",
        o=org,
    )
    assert stored["config"] == {"merchant_id": "M-42", "salt_index": 1}
    audit = [
        a
        for a in await audit_rows(admin_session, org)
        if a["action"] == "payment.merchant_account.connected"
    ]
    assert audit[0]["after"]["config"] == {"merchant_id": "M-42", "salt_index": 1}
    assert SECRET not in str(audit)

    # A rotation that sends no config keeps it; one that sends config replaces it.
    kept = await _put(client, org)
    assert kept.json()["config"] == {"merchant_id": "M-42", "salt_index": 1}
    replaced = await _put(client, org, config={"merchant_id": "M-43"})
    assert replaced.json()["config"] == {"merchant_id": "M-43"}


@pytest.mark.parametrize(
    "bad",
    [
        {"Bad Key": "x"},
        {f"k{i}": "v" for i in range(11)},
        {"k": "x" * 201},
        {"k": ["nested"]},
        {"k": {"nested": 1}},
    ],
)
async def test_config_is_bounded_scalar_and_snake_case(client: AsyncClient, bad: object) -> None:
    response = await _put(client, uuid.uuid4(), config=bad)
    assert response.status_code == 422


async def test_unknown_fields_are_still_refused(client: AsyncClient) -> None:
    assert (await _put(client, uuid.uuid4(), secret_config="x")).status_code == 422


async def test_writing_config_needs_the_write_permission(client: AsyncClient) -> None:
    with as_principal(principal_for(uuid.uuid4(), [READ])):
        response = await client.put(
            ACCOUNT,
            json={
                "provider": "cashfree",
                "key_id": APP_ID,
                "key_secret": SECRET,
                "mode": "sandbox",
                "config": {"a": "b"},
            },
        )
    assert response.status_code == 403


async def test_another_organization_never_sees_the_config(client: AsyncClient) -> None:
    org, other = uuid.uuid4(), uuid.uuid4()
    await _put(client, org, config={"merchant_id": "M-42"})
    with as_principal(principal_for(other, [READ, WRITE])):
        response = await client.get(ACCOUNT)
    assert response.json()["configured"] is False
    assert response.json()["config"] == {}
