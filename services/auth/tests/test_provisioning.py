"""`FakeProvisioningClient`: the in-memory double tests use to stand in for account-service's
write-side internal API, without a second service running.

Covers the happy path and the two failure modes `_resolve_or_provision_identity` depends on:
a duplicate email/slug conflict on signup, and an invitation whose email does not match the
one the identity provider verified.

Also covers `HttpProvisioningClient._post`'s own defence, independent of anything that calls
it: account-service answering 409/404 with a body that is not JSON (a proxy error page, a
misrouted gateway hop, an empty body on timeout) must still raise the documented
`ConflictProblem`/`NotFoundProblem`, never an unhandled `json.JSONDecodeError` — same class of
bug, and the same `try`/`except (ValueError, AttributeError)` fix, as
`console.delegation._problem_detail`.
"""

import uuid

import httpx
import pytest

from app.config import settings
from app.provisioning import FakeProvisioningClient, HttpProvisioningClient
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


class TestHttpProvisioningClientNonJsonErrorBodies:
    """Reviewer finding: `_post` used to call `response.json()` unguarded in the 409/404
    branches. A non-JSON error body (an HTML proxy page, an empty body) made that raise an
    unhandled `json.JSONDecodeError` instead of the `ConflictProblem`/`NotFoundProblem` the
    whole method exists to produce."""

    async def test_a_non_json_409_body_still_raises_conflict(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, content=b"<html>502 Bad Gateway</html>")

        client = HttpProvisioningClient("http://account", settings())
        client._client = httpx.AsyncClient(
            base_url="http://account", transport=httpx.MockTransport(handler)
        )
        with pytest.raises(ConflictProblem) as excinfo:
            await client.signup_federated(
                organization_name="Acme", slug="acme", full_name="Ada", email="ada@acme.com"
            )
        await client.aclose()
        assert excinfo.value.detail == "That account already exists"

    async def test_a_non_json_404_body_still_raises_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, content=b"")

        client = HttpProvisioningClient("http://account", settings())
        client._client = httpx.AsyncClient(
            base_url="http://account", transport=httpx.MockTransport(handler)
        )
        with pytest.raises(NotFoundProblem) as excinfo:
            await client.accept_invitation_federated(token="tok-1", email="ada@acme.com")
        await client.aclose()
        assert excinfo.value.detail == "That invitation is not valid"

    async def test_a_json_409_body_with_detail_is_used_verbatim(self) -> None:
        """The control: a well-formed problem+json body still wins over the fallback."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"detail": "The address 'acme' is already taken"})

        client = HttpProvisioningClient("http://account", settings())
        client._client = httpx.AsyncClient(
            base_url="http://account", transport=httpx.MockTransport(handler)
        )
        with pytest.raises(ConflictProblem) as excinfo:
            await client.signup_federated(
                organization_name="Acme", slug="acme", full_name="Ada", email="ada@acme.com"
            )
        await client.aclose()
        assert excinfo.value.detail == "The address 'acme' is already taken"
