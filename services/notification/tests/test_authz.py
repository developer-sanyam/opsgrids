"""Authorization, as negative assertions — against real PostgreSQL.

Proving a call is refused is worth more than proving one succeeds. Every principal route carries
a check that a caller missing the permission, the entitlement, or the ORG scope floor is turned
away. The one that matters most here is the split between reading a *rule* and reading a
*message*: the second exposes the rendered body — the invoice total, the payslip line — and a
role that can author automations must not get it for free.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest

from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


T_READ = "notification.delivery.template.read"
T_WRITE = "notification.delivery.template.write"
M_READ = "notification.delivery.message.read"
M_SEND = "notification.delivery.message.send"
P_WRITE = "notification.delivery.preference.write"

TEMPLATE_READ_ROUTES = [
    ("GET", "/api/v1/notifications/templates"),
    ("GET", "/api/v1/notifications/rules"),
    ("GET", "/api/v1/notifications/preferences"),
    ("GET", "/api/v1/notifications/subscribable-events"),
    ("GET", "/api/v1/notifications/provider-templates"),
]

WRITE_BODIES: dict[str, dict] = {
    "/api/v1/notifications/templates": {
        "code": "t",
        "channel": "email",
        "subject": "S",
        "body": "B",
    },
    "/api/v1/notifications/rules": {
        "code": "r",
        "event_type": "support.desk.ticket.created",
        "template_code": "t",
        "channel": "email",
        "recipient_path": "requester_email",
    },
    "/api/v1/notifications/preferences": {
        "recipient": "a@b.example.com",
        "channel": "email",
    },
    "/api/v1/notifications/provider-templates": {
        "code": "pt",
        "channel": "whatsapp",
        "provider_name": "meta",
    },
}


async def _call(client, method: str, path: str):
    if method == "GET":
        return await client.get(path)
    return await client.request(method, path, json=WRITE_BODIES.get(path, {}))


@pytest.mark.parametrize(("method", "path"), TEMPLATE_READ_ROUTES)
async def test_reads_refuse_a_caller_without_the_permission(client, org_id, method, path) -> None:
    with as_principal(principal_for(org_id, [])):
        response = await _call(client, method, path)
    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path"), TEMPLATE_READ_ROUTES)
async def test_reads_refuse_a_caller_without_the_entitlement(client, org_id, method, path) -> None:
    """402, not 403: the permissions are fine, the subscription is not."""
    with as_principal(principal_for(org_id, [f"{T_READ}:ORG"], entitlements=[])):
        response = await _call(client, method, path)
    assert response.status_code == 402


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/notifications/templates",
        "/api/v1/notifications/rules",
        "/api/v1/notifications/preferences",
        "/api/v1/notifications/provider-templates",
    ],
)
async def test_writes_refuse_a_reader(client, org_id, path) -> None:
    with as_principal(principal_for(org_id, [f"{T_READ}:ORG"])):
        response = await client.put(path, json=WRITE_BODIES[path])
    assert response.status_code == 403


async def test_provider_templates_still_require_the_notification_permission(client, org_id) -> None:
    """The route this bug moved Engage off of. Unchanged: an Engage grant must not reach it —
    only `notification.delivery.template.*`, gated on the `notification` entitlement, does."""
    with as_principal(
        principal_for(
            org_id,
            ["engage.whatsapp.template.read:ORG", "engage.whatsapp.template.write:ORG"],
            entitlements=["engage"],
        )
    ):
        read = await client.get("/api/v1/notifications/provider-templates")
        write = await client.put(
            "/api/v1/notifications/provider-templates",
            json=WRITE_BODIES["/api/v1/notifications/provider-templates"],
        )
    assert read.status_code == 402  # not entitled to `notification` at all
    assert write.status_code == 402

    with as_principal(principal_for(org_id, [f"{T_READ}:ORG"])):
        read = await client.get("/api/v1/notifications/provider-templates")
    assert read.status_code == 200


async def test_reading_messages_needs_its_own_permission(client, org_id) -> None:
    """The load-bearing split. Template reading is not message reading."""
    with as_principal(principal_for(org_id, [f"{T_READ}:ORG"])):
        response = await client.get("/api/v1/notifications/messages")
    assert response.status_code == 403


async def test_authoring_a_rule_does_not_grant_reading_bodies(client, org_id) -> None:
    with as_principal(principal_for(org_id, [f"{T_WRITE}:ORG", f"{T_READ}:ORG"])):
        response = await client.get("/api/v1/notifications/messages")
    assert response.status_code == 403


async def test_reading_messages_is_allowed_with_the_right_permission(client, org_id) -> None:
    with as_principal(principal_for(org_id, [f"{M_READ}:ORG"])):
        response = await client.get("/api/v1/notifications/messages")
    assert response.status_code == 200


async def test_sending_directly_needs_the_send_permission(client, org_id) -> None:
    body = {"template_code": "t", "channel": "email", "to_address": "a@b.example.com"}
    with as_principal(principal_for(org_id, [f"{M_READ}:ORG"])):
        response = await client.post("/api/v1/notifications/messages", json=body)
    assert response.status_code == 403


async def test_a_narrower_scope_than_org_is_refused(client, org_id) -> None:
    """Notification governs what the whole organization sends: OWN is not a rung here."""
    with as_principal(principal_for(org_id, [f"{T_READ}:OWN"])):
        response = await client.get("/api/v1/notifications/templates")
    assert response.status_code == 403


async def test_setting_a_preference_needs_the_preference_permission(client, org_id) -> None:
    with as_principal(principal_for(org_id, [f"{T_WRITE}:ORG"])):
        response = await client.put(
            "/api/v1/notifications/preferences",
            json=WRITE_BODIES["/api/v1/notifications/preferences"],
        )
    assert response.status_code == 403


async def test_a_rule_on_an_unpublished_event_is_a_422(client, org_id) -> None:
    """The authoring guard, through the API: an automation that could never fire is refused."""
    body = dict(WRITE_BODIES["/api/v1/notifications/rules"])
    body["event_type"] = "support.desk.ticket.exploded"
    with as_principal(principal_for(org_id, [f"{T_WRITE}:ORG"])):
        response = await client.put("/api/v1/notifications/rules", json=body)
    assert response.status_code == 422


async def test_a_direct_send_to_a_malformed_address_is_a_422(client, org_id) -> None:
    body = {"template_code": "t", "channel": "whatsapp", "to_address": "7976603446"}
    with as_principal(principal_for(org_id, [f"{M_SEND}:ORG"])):
        response = await client.post("/api/v1/notifications/messages", json=body)
    assert response.status_code == 422
