"""The directory this service authenticates against.

auth-service owns credentials; account-service owns *who exists*. Rather than reading
across a schema boundary — which would weld the two together and forfeit a later
database-per-service split — it asks over the internal API.

A `Protocol` rather than a concrete client, because the whole login flow is worth testing
without a second service running. The fake in the tests implements this, and the real
client is a thin HTTP wrapper over the same shape.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from platform_core.config import ServiceSettings
from platform_core.errors import UpstreamUnavailableProblem
from platform_core.internal_auth import mint_service_token
from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("auth.directory")

#: What a domain looks like on the wire: an IDNA A-label, lower-cased. The same shape
#: `services.normalise_sso_domain` guarantees its output has — enforced again here, at the one
#: place that builds a URL from it, because `urllib.parse.quote` does not encode `.`, so a value
#: made of dots (`..`) survives percent-encoding untouched and is a real dot-segment that httpx
#: collapses before the request leaves the process.
_DOMAIN_SHAPE = re.compile(r"^[a-z0-9.-]+$")


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
        # `email` travels as a query parameter, which httpx already percent-encodes — this one
        # was never the traversal risk. Kept alongside the two below for the same reason they
        # are fixed: a reviewer should not have to know which internal calls are safe from
        # first principles.
        body = await self._fetch("/internal/v1/users/by-email", {"email": email})
        return self._parse(body) if body else None

    async def get(self, user_id: uuid.UUID) -> DirectoryUser | None:
        body = await self._fetch(f"/internal/v1/users/{quote(str(user_id), safe='')}")
        return self._parse(body) if body else None

    async def resolve_domain(self, domain: str) -> DirectoryDomain | None:
        """Which organization owns an email domain, and whether it proved it.

        `None` means nobody has claimed it. A claimed-but-unverified domain comes back with
        `verified=False` rather than as `None`, because the caller refuses both with one
        message and logs which it was — see `services.sso_start`.

        **The path segment is percent-encoded, always** — round 3's finding: `domain` used to
        be spliced into the path by f-string, and `services.normalise_sso_domain` used to
        return an unencodeable input *unchanged* rather than refusing it, so a `domain` value
        like `../users/by-email?email=victim@example.com` reached this call, and httpx's own
        URL-joining collapsed the `..` before the request ever left the process — landing on a
        *different* internal route than the one this method calls, with account-service's real
        response from that other route reaching `_parse` below and failing in whatever way
        that route's response shape happens to fail in. `normalise_sso_domain` is now refusing
        that input itself (nothing not shaped like a domain survives it), but this is fixed
        here too, independently: whatever reaches this method, `quote` guarantees it can only
        ever address *this* path segment, never another route.

        **`quote` alone is not enough, and the shape gate below is why.** `quote` does not
        encode `.`, so `..` — or any all-dots value — passes through it unchanged and is a real
        dot-segment; `""` would address the collection root. Anything that is empty, made only
        of dots, or contains a character outside `[a-z0-9.-]` is therefore answered `None`
        without a request being made at all.
        """
        if not _DOMAIN_SHAPE.match(domain) or not domain.strip("."):
            # Not a domain, so not a request: nothing is sent, and "not configured" is the
            # answer — the same one a domain nobody claimed gets. `""`, `.`, `..` and anything
            # with a character an A-label cannot hold all land here.
            return None
        body = await self._fetch(f"/internal/v1/domains/{quote(domain, safe='')}")
        if not body:
            return None
        try:
            return DirectoryDomain(
                domain=body["domain"],
                organization_id=uuid.UUID(body["organization_id"]),
                organization_slug=body["organization_slug"],
                organization_status=body["organization_status"],
                verified=bool(body["verified"]),
            )
        except (KeyError, ValueError, TypeError):
            # A body that does not have this shape is not a domain answer — reaching here
            # would mean the request landed on some other endpoint, or account-service
            # answered something malformed. Either way "not configured" is the honest and
            # safe reading: never a 500 on a request that was never going to succeed anyway.
            log.error("directory.resolve_domain_malformed_body", domain=domain)
            return None
