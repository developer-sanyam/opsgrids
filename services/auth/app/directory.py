"""The directory this service authenticates against.

auth-service owns credentials; account-service owns *who exists*. Rather than reading
across a schema boundary — which would weld the two together and forfeit a later
database-per-service split — it asks over the internal API.

A `Protocol` rather than a concrete client, because the whole login flow is worth testing
without a second service running. The fake in the tests implements this, and the real
client is a thin HTTP wrapper over the same shape.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from platform_core.config import ServiceSettings
from platform_core.errors import UpstreamUnavailableProblem
from platform_core.internal_auth import mint_service_token
from platform_core.logging import correlation_id_ctx


@dataclass(frozen=True, slots=True)
class DirectoryMembership:
    """One organization this person belongs to, as authentication needs to see it."""

    membership_id: uuid.UUID
    organization_id: uuid.UUID
    organization_slug: str
    organization_name: str
    status: str
    roles: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    permission_version: int = 0
    employee_id: uuid.UUID | None = None

    @property
    def is_active(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True, slots=True)
class DirectoryUser:
    user_id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    memberships: list[DirectoryMembership] = field(default_factory=list)

    def active_memberships(self) -> list[DirectoryMembership]:
        return [m for m in self.memberships if m.is_active]

    def membership_for(self, organization_id: uuid.UUID) -> DirectoryMembership | None:
        return next(
            (m for m in self.active_memberships() if m.organization_id == organization_id), None
        )

    def membership_by_slug(self, slug: str) -> DirectoryMembership | None:
        needle = slug.lower()
        return next(
            (m for m in self.active_memberships() if m.organization_slug.lower() == needle), None
        )


@dataclass(frozen=True, slots=True)
class DirectoryDomain:
    """An email domain and the organization that claimed it.

    `verified` is the whole point of carrying this across the boundary. ADR-0000 §1 permits a
    domain to select an identity *provider*, and only a DNS-verified one — so the flag has to
    travel with the answer rather than being assumed by whoever asked.
    """

    domain: str
    organization_id: uuid.UUID
    organization_slug: str
    organization_status: str
    verified: bool

    @property
    def is_usable(self) -> bool:
        """Verified, and owned by an organization that is not suspended."""
        return self.verified and self.organization_status != "suspended"


class Directory(Protocol):
    """What authentication needs to know about people. Nothing more."""

    async def find_by_email(self, email: str) -> DirectoryUser | None: ...

    async def get(self, user_id: uuid.UUID) -> DirectoryUser | None: ...

    async def resolve_domain(self, domain: str) -> DirectoryDomain | None: ...


class HttpDirectory:
    """The real client. Talks to account-service over the internal API."""

    def __init__(self, base_url: str, settings: ServiceSettings, *, timeout: float = 5.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)
        self._settings = settings

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        # Minted per call: short-lived, and addressed to account-service by name so it cannot
        # be replayed against anything else. The signing key never leaves this process.
        headers = {
            "X-Internal-Token": mint_service_token(self._settings, audience="account"),
            "X-Calling-Service": "auth",
        }
        correlation = correlation_id_ctx.get()
        if correlation:
            headers["X-Correlation-ID"] = correlation
        return headers

    async def _fetch(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        try:
            response = await self._client.get(path, headers=self._headers(), params=params)
        except httpx.HTTPError as exc:
            # A directory outage must not look like "wrong password" — that would lock
            # every correct credential out with a misleading message.
            raise UpstreamUnavailableProblem("The directory is unavailable") from exc
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    @staticmethod
    def _parse(body: dict[str, Any]) -> DirectoryUser:
        return DirectoryUser(
            user_id=uuid.UUID(body["user_id"]),
            email=body["email"],
            full_name=body["full_name"],
            is_active=body["is_active"],
            memberships=[
                DirectoryMembership(
                    membership_id=uuid.UUID(m["membership_id"]),
                    organization_id=uuid.UUID(m["organization_id"]),
                    organization_slug=m["organization_slug"],
                    organization_name=m["organization_name"],
                    status=m["status"],
                    roles=list(m.get("roles", [])),
                    locations=list(m.get("locations", [])),
                    permission_version=int(m.get("permission_version", 0)),
                    employee_id=uuid.UUID(m["employee_id"]) if m.get("employee_id") else None,
                )
                for m in body.get("memberships", [])
            ],
        )

    async def find_by_email(self, email: str) -> DirectoryUser | None:
        body = await self._fetch("/internal/v1/users/by-email", {"email": email})
        return self._parse(body) if body else None

    async def get(self, user_id: uuid.UUID) -> DirectoryUser | None:
        body = await self._fetch(f"/internal/v1/users/{user_id}")
        return self._parse(body) if body else None

    async def resolve_domain(self, domain: str) -> DirectoryDomain | None:
        """Which organization owns an email domain, and whether it proved it.

        `None` means nobody has claimed it. A claimed-but-unverified domain comes back with
        `verified=False` rather than as `None`, because the caller refuses both with one
        message and logs which it was — see `services.sso_start`.
        """
        body = await self._fetch(f"/internal/v1/domains/{domain}")
        if not body:
            return None
        return DirectoryDomain(
            domain=body["domain"],
            organization_id=uuid.UUID(body["organization_id"]),
            organization_slug=body["organization_slug"],
            organization_status=body["organization_status"],
            verified=bool(body["verified"]),
        )
