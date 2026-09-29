"""`/engage/email/templates` — the fix for the production bug this file is named for.

Every Engage customer, including the platform owner's own organization, saw "Missing
permission: notification.delivery.template.read" on the Email -> Templates screen: it called a
`notification`-catalogue route no Engage role ever granted. The fix gives Engage its own
permission (`engage.email.template.read`/`.write`) and its own route, reading and writing the
same `Template` rows `notification`'s own route always has — see `test_authz.py` for the
assertion that route is unchanged. Mirrors `test_engage_whatsapp_templates.py`, the same fix for
the same bug on the WhatsApp screen two days earlier.

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

TEMPLATES = "/api/v1/engage/email/templates"

TEMPLATE_READ = "engage.email.template.read"
TEMPLATE_WRITE = "engage.email.template.write"

BODY = {
    "code": "welcome_email",
    "channel": "email",
    "subject": "Welcome, {{first_name}}!",
    "body": "Hi {{first_name}}, thanks for signing up.",
    "description": "Sent right after signup.",
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


async def test_an_admin_can_create_and_then_list_a_template(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    with as_principal(admin(org_id)):
        put_response = await client.put(TEMPLATES, json=BODY)
    assert put_response.status_code == 200
    created = put_response.json()
    assert created["id"] == "welcome_email"
    assert created["channel"] == "email"
    assert created["subject"] == BODY["subject"]
    assert sorted(created["placeholders"]) == ["first_name"]

    with as_principal(admin(org_id)):
        list_response = await client.get(TEMPLATES)
    assert list_response.status_code == 200
    body = list_response.json()
    assert [item["id"] for item in body["items"]] == ["welcome_email"]
    assert body["total"] == 1


async def test_a_put_on_the_same_code_replaces_rather_than_duplicates(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    """Also the regression test for the `MissingGreenlet` `upsert_provider_template` hit for the
    same reason: a second `PUT` of the same code must come back clean, not crash serializing an
    expired `updated_at`."""
    with as_principal(admin(org_id)):
        await client.put(TEMPLATES, json=BODY)
        second = await client.put(TEMPLATES, json={**BODY, "subject": "Updated subject"})
    assert second.status_code == 200
    assert second.json()["subject"] == "Updated subject"

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
            models.Template(
                organization_id=other,
                created_by="system:test",
                code="theirs",
                channel="email",
                subject="Not yours",
                body="Not this organization's template.",
                description="",
            )
        )

    with as_principal(admin(org_id)):
        response = await client.get(TEMPLATES)
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_an_upsert_never_touches_another_organizations_row_of_the_same_code(
    client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
) -> None:
    """`(organization_id, code, channel)` is unique *per organization* — two tenants may each own
    a `welcome_email`, and one organization's `PUT` must find and replace only its own."""
    other = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            models.Template(
                organization_id=other,
                created_by="system:test",
                code=BODY["code"],
                channel=BODY["channel"],
                subject="Their own subject",
                body="Their own wording.",
                description="",
            )
        )

    with as_principal(admin(org_id)):
        response = await client.put(TEMPLATES, json=BODY)
    assert response.status_code == 200
    assert response.json()["subject"] == BODY["subject"]

    async with admin_session.begin():
        result = await admin_session.execute(
            select(models.Template).where(models.Template.code == BODY["code"])
        )
        rows = result.scalars().all()
    by_org = {row.organization_id: row for row in rows}
    assert len(by_org) == 2
    assert by_org[org_id].subject == BODY["subject"]
    assert by_org[other].subject == "Their own subject"
