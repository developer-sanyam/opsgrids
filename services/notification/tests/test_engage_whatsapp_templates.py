"""`/engage/whatsapp/templates` — the fix for the production bug this file is named for.

Every Engage customer, including the platform owner's own organization, saw "Couldn't load your
WhatsApp templates · Missing permission: notification.delivery.template.read" on this screen: it
called a `notification`-catalogue route no Engage role ever granted. The fix gives Engage its own
permission (`engage.whatsapp.template.read`/`.write`) and its own route, reading and writing the
same `ProviderTemplate` rows `notification`'s own route always has — see `test_authz.py` for the
assertion that route is unchanged.

Real PostgreSQL as the app role, like the rest of this service's integration suite — row-level
security has to be visible in the test, not only in production.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

TEMPLATES = "/api/v1/engage/whatsapp/templates"

TEMPLATE_READ = "engage.whatsapp.template.read"
TEMPLATE_WRITE = "engage.whatsapp.template.write"

BODY = {
    "code": "delivery_confirmed",
    "channel": "whatsapp",
    "provider_name": "meta",
    "language": "en",
    "category": "utility",
    "preview_text": "Hi {{1}}, your shipment is out for delivery today.",
}


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def admin(org_id: uuid.UUID) -> Principal:
    return principal_for(
        org_id, [f"{TEMPLATE_READ}:ORG", f"{TEMPLATE_WRITE}:ORG"], entitlements=["engage"]
    )


def viewer(org_id: uuid.UUID) -> Principal:
    return principal_for(org_id, [f"{TEMPLATE_READ}:ORG"], entitlements=["engage"])


# --------------------------------------------------------------------- happy path


async def test_an_admin_can_register_and_then_list_a_template(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    with as_principal(admin(org_id)):
        put_response = await client.put(TEMPLATES, json=BODY)
    assert put_response.status_code == 200
    created = put_response.json()
    assert created["id"] == "delivery_confirmed"
    assert created["channel"] == "whatsapp"
    assert created["status"] == "draft"
    assert created["sendable"] is False

    with as_principal(admin(org_id)):
        list_response = await client.get(TEMPLATES, params={"channel": "whatsapp"})
    assert list_response.status_code == 200
    body = list_response.json()
    assert [item["id"] for item in body["items"]] == ["delivery_confirmed"]
    assert body["total"] == 1


async def test_a_put_on_the_same_code_replaces_rather_than_duplicates(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    with as_principal(admin(org_id)):
        await client.put(TEMPLATES, json=BODY)
        second = await client.put(TEMPLATES, json={**BODY, "preview_text": "Updated wording."})
    assert second.status_code == 200
    assert second.json()["preview_text"] == "Updated wording."

    with as_principal(admin(org_id)):
        listed = await client.get(TEMPLATES)
    assert listed.json()["total"] == 1


# --------------------------------------------------------------------- permission boundaries


async def test_a_viewer_can_read_but_not_write(client: AsyncClient, org_id: uuid.UUID) -> None:
    with as_principal(viewer(org_id)):
        read_response = await client.get(TEMPLATES)
        write_response = await client.put(TEMPLATES, json=BODY)
    assert read_response.status_code == 200
    assert write_response.status_code == 403


async def test_a_caller_with_no_engage_role_is_refused(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    with as_principal(principal_for(org_id, [], entitlements=["engage"])):
        read_response = await client.get(TEMPLATES)
        write_response = await client.put(TEMPLATES, json=BODY)
    assert read_response.status_code == 403
    assert write_response.status_code == 403


async def test_an_org_not_entitled_to_engage_is_refused(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    """402, not 403: the permission is held, the subscription is not — the same distinction
    every other Engage route makes (see `test_engage_setup.py`)."""
    with as_principal(
        principal_for(org_id, [f"{TEMPLATE_READ}:ORG", f"{TEMPLATE_WRITE}:ORG"], entitlements=[])
    ):
        read_response = await client.get(TEMPLATES)
        write_response = await client.put(TEMPLATES, json=BODY)
    assert read_response.status_code == 402
    assert write_response.status_code == 402


async def test_the_notification_entitlement_does_not_unlock_this_route(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    """The two products are sold separately — holding `notification` must not substitute for
    `engage` here, the mirror of `test_authz.py`'s guarantee in the other direction."""
    with as_principal(
        principal_for(
            org_id, [f"{TEMPLATE_READ}:ORG", f"{TEMPLATE_WRITE}:ORG"], entitlements=["notification"]
        )
    ):
        response = await client.get(TEMPLATES)
    assert response.status_code == 402


# --------------------------------------------------------------------- cross-tenant isolation


async def test_another_organizations_templates_are_invisible(
    client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
) -> None:
    other = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            models.ProviderTemplate(
                organization_id=other,
                created_by="system:test",
                code="theirs",
                channel="whatsapp",
                provider_name="meta",
                language="en",
                category="utility",
                parameter_format="positional",
                status="approved",
                preview_text="Not this organization's template.",
            )
        )

    with as_principal(admin(org_id)):
        response = await client.get(TEMPLATES)
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_an_upsert_never_touches_another_organizations_row_of_the_same_code(
    client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
) -> None:
    """`(organization_id, code, channel)` is unique *per organization*
    (`uq_provider_templates_org_code_chan`) — two tenants may each own a `delivery_confirmed`,
    and one organization's `PUT` must find and replace only its own."""
    other = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            models.ProviderTemplate(
                organization_id=other,
                created_by="system:test",
                code=BODY["code"],
                channel=BODY["channel"],
                provider_name="meta",
                language="en",
                category="utility",
                parameter_format="positional",
                status="approved",
                preview_text="Their own wording, already approved.",
            )
        )

    with as_principal(admin(org_id)):
        response = await client.put(TEMPLATES, json=BODY)
    assert response.status_code == 200
    # Ours came back freshly created, not the other tenant's approved row.
    assert response.json()["status"] == "draft"

    async with admin_session.begin():
        result = await admin_session.execute(
            select(models.ProviderTemplate).where(models.ProviderTemplate.code == BODY["code"])
        )
        rows = result.scalars().all()
    by_org = {row.organization_id: row for row in rows}
    assert len(by_org) == 2
    assert by_org[org_id].preview_text == BODY["preview_text"]
    assert by_org[other].preview_text == "Their own wording, already approved."
    assert by_org[other].status == "approved"
