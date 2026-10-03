"""`FakeProvisioningClient`: the in-memory double tests use to stand in for account-service's
write-side internal API, without a second service running.

Covers the happy path and the two failure modes `_resolve_or_provision_identity` depends on:
a duplicate email/slug conflict on signup, and an invitation whose email does not match the
one the identity provider verified.
"""

import uuid

import pytest

from app.provisioning import FakeProvisioningClient
from platform_core.errors import ConflictProblem, NotFoundProblem


async def test_signup_federated_happy_path():
    client = FakeProvisioningClient()
    result = await client.signup_federated(
        organization_name="Acme", slug="acme", full_name="Ada", email="ada@acme.com"
    )
    assert isinstance(result.organization_id, uuid.UUID)
    assert isinstance(result.user_id, uuid.UUID)


async def test_signup_federated_duplicate_email_conflicts():
    client = FakeProvisioningClient()
    await client.signup_federated(
        organization_name="Acme", slug="acme", full_name="Ada", email="ada@acme.com"
    )
    with pytest.raises(ConflictProblem):
        await client.signup_federated(
            organization_name="Acme Two", slug="acme-two", full_name="Ada", email="ada@acme.com"
        )


async def test_accept_invitation_federated_email_mismatch_is_not_found():
    client = FakeProvisioningClient()
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    client.invitations["tok-1"] = ("invitee@acme.com", org_id, user_id, False)
    with pytest.raises(NotFoundProblem):
        await client.accept_invitation_federated(token="tok-1", email="attacker@evil.com")
