"""auth-service's write path into account-service's directory.

Unlike `directory.py` (read-only — "what authentication needs to know about people"), this
is the one place auth-service **writes** into account-service's schema, and it does so
exclusively through account-service's own internal endpoints — never a direct table write,
never a cross-schema FK. Used only by the OAuth social-login flow (`services.py`'s
`_resolve_or_provision_identity`), when a verified email has no existing directory row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

import httpx

from platform_core.config import ServiceSettings
from platform_core.errors import ConflictProblem, NotFoundProblem, UpstreamUnavailableProblem
from platform_core.internal_auth import mint_service_token
from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("auth.provisioning")


@dataclass(frozen=True, slots=True)
class ProvisionedAccount:
    organization_id: uuid.UUID
    user_id: uuid.UUID


class ProvisioningClient(Protocol):
    async def signup_federated(
        self, *, organization_name: str, slug: str, full_name: str, email: str
    ) -> ProvisionedAccount: ...

    async def accept_invitation_federated(
        self, *, token: str, email: str
    ) -> ProvisionedAccount: ...


class HttpProvisioningClient:
    """The real client. Talks to account-service's internal API, same posture as `HttpDirectory`."""

    def __init__(self, base_url: str, settings: ServiceSettings, *, timeout: float = 8.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)
        self._settings = settings

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-Internal-Token": mint_service_token(self._settings, audience="account"),
            "X-Calling-Service": "auth",
        }
        correlation = correlation_id_ctx.get()
        if correlation:
            headers["X-Correlation-ID"] = correlation
        return headers

    async def _post(self, path: str, body: dict[str, str]) -> dict[str, str]:
        try:
            response = await self._client.post(path, headers=self._headers(), json=body)
        except httpx.HTTPError as exc:
            raise UpstreamUnavailableProblem("The directory is unavailable") from exc
        if response.status_code == 409:
            raise ConflictProblem(response.json().get("detail", "That account already exists"))
        if response.status_code == 404:
            raise NotFoundProblem(response.json().get("detail", "That invitation is not valid"))
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def signup_federated(
        self, *, organization_name: str, slug: str, full_name: str, email: str
    ) -> ProvisionedAccount:
        body = await self._post(
            "/internal/v1/signup-federated",
            {
                "organization_name": organization_name,
                "slug": slug,
                "full_name": full_name,
                "email": email,
            },
        )
        return ProvisionedAccount(
            organization_id=uuid.UUID(body["organization_id"]), user_id=uuid.UUID(body["user_id"])
        )

    async def accept_invitation_federated(self, *, token: str, email: str) -> ProvisionedAccount:
        body = await self._post(
            "/internal/v1/invitations/accept-federated", {"token": token, "email": email}
        )
        return ProvisionedAccount(
            organization_id=uuid.UUID(body["organization_id"]), user_id=uuid.UUID(body["user_id"])
        )


class FakeProvisioningClient:
    """In-memory test double. Tests seed `.orgs`/`.invitations` and assert on `.calls`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self._next_org_id = uuid.uuid4()
        self._next_user_id = uuid.uuid4()
        self.existing_emails: set[str] = set()
        self.existing_slugs: set[str] = set()
        #: token -> (email, organization_id, user_id, revoked_or_expired: bool)
        self.invitations: dict[str, tuple[str, uuid.UUID, uuid.UUID, bool]] = {}

    async def signup_federated(
        self, *, organization_name: str, slug: str, full_name: str, email: str
    ) -> ProvisionedAccount:
        self.calls.append(("signup_federated", {"email": email, "slug": slug}))
        if slug in self.existing_slugs:
            raise ConflictProblem(f"The address {slug!r} is already taken")
        if email in self.existing_emails:
            raise ConflictProblem("That email already has an account. Sign in instead.")
        self.existing_emails.add(email)
        self.existing_slugs.add(slug)
        return ProvisionedAccount(organization_id=self._next_org_id, user_id=self._next_user_id)

    async def accept_invitation_federated(self, *, token: str, email: str) -> ProvisionedAccount:
        self.calls.append(("accept_invitation_federated", {"token": token, "email": email}))
        entry = self.invitations.get(token)
        if entry is None or entry[3]:
            raise NotFoundProblem("That invitation is not valid")
        invited_email, org_id, user_id, _ = entry
        if invited_email.lower() != email.lower():
            raise NotFoundProblem("That invitation is not valid")
        return ProvisionedAccount(organization_id=org_id, user_id=user_id)
