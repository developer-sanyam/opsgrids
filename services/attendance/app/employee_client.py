"""The TEAM backfill's HTTP client — employee-service's one-time hierarchy export.

Talks to `GET /internal/v1/hierarchy` (ADR-0006 §2.1) over the service-token internal API, the
same discipline approval's `employee_client.py` follows, and for the same reason: a service-token
client rather than a cross-schema read (ADR-0000 §10), because employee's availability being a
precondition of a one-time backfill is an acceptable trade in a way it would not be on the read
path. **Nothing that serves a request ever calls this.**

Every number is ADR-0006 §2.1's failure-contract table, unchanged:

- **Page size 500.**
- **Timeout 10 s total, 2 s connect.**
- **Retry 5 attempts, backoff 1·2·4·8·16 s, full jitter** — on connection errors, timeouts and
  5xx **only**. A 4xx means the audience, the secret, the issuer or the cursor is wrong, and
  retrying it repeats the mistake.
- **Service token minted per HTTP request**, not per page: `service_token_ttl_seconds` is 60 and
  the backoff alone can exceed that within one page's retries.

**The organization is bound into the token** (`claims={"org": ...}`), so a captured token cannot
be replayed against a different tenant than the one it was minted for. Employee's hierarchy route
requires the claim from this caller onward.

**What is kept from a page, and what is not.** The endpoint returns each employee's name and
employee number as well (approval needs them). This client parses four fields — id, manager,
status, version — and the rest never leave the response object, so a name cannot end up in this
service's tables or logs by way of a helper that forgot to skip it.

This module owns the HTTP call and its retry envelope. Pacing between pages, the transaction per
page, and resuming by cursor are `app/backfill.py`'s — they are properties of the run, not of one
request.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from platform_core.config import ServiceSettings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("attendance.employee_client")

#: ADR-0006 §2.1.
PAGE_SIZE = 500
TOTAL_TIMEOUT_SECONDS = 10.0
CONNECT_TIMEOUT_SECONDS = 2.0
MAX_ATTEMPTS = 5
#: Full jitter over 1, 2, 4, 8, 16 seconds.
BACKOFF_BASE_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0)

#: The audience employee-service's service-token check compares against.
EMPLOYEE_AUDIENCE = "employee"


@dataclass(frozen=True, slots=True)
class TeamItem:
    """The four fields of one `GET /internal/v1/hierarchy` row this service keeps."""

    employee_id: uuid.UUID
    reporting_manager_id: uuid.UUID | None
    status: str
    #: `None` means an un-upgraded employee row — ADR-0006 §2.1 says it "is ignored, never treated
    #: as version 0", so `app/backfill.py` drops it rather than passing 0 to the upsert.
    hierarchy_version: int | None


@dataclass(frozen=True, slots=True)
class TeamPage:
    items: list[TeamItem]
    next_cursor: uuid.UUID | None


def _parse_item(raw: dict[str, Any]) -> TeamItem:
    """Parse at the boundary: typed values or a refusal, never a string that looks like one."""
    return TeamItem(
        employee_id=uuid.UUID(str(raw["employee_id"])),
        reporting_manager_id=(
            uuid.UUID(str(raw["reporting_manager_id"])) if raw.get("reporting_manager_id") else None
        ),
        status=str(raw["status"]),
        hierarchy_version=(
            int(raw["hierarchy_version"]) if raw.get("hierarchy_version") is not None else None
        ),
    )


class TeamSource(Protocol):
    """What the backfill needs from employee-service. A test substitutes a stub."""

    async def fetch_page(
        self, organization_id: uuid.UUID, *, cursor: uuid.UUID | None, limit: int = PAGE_SIZE
    ) -> TeamPage: ...


class RetryExhaustedError(RuntimeError):
    """Every attempt failed with a retryable error — a connection problem, a timeout, or a 5xx."""


class HttpTeamSource:
    """The real client. Talks to employee-service's internal hierarchy export."""

    def __init__(self, base_url: str, settings: ServiceSettings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(TOTAL_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_page(
        self, organization_id: uuid.UUID, *, cursor: uuid.UUID | None, limit: int = PAGE_SIZE
    ) -> TeamPage:
        """One page, retried per the table in the module docstring."""
        params: dict[str, str | int] = {"organization_id": str(organization_id), "limit": limit}
        if cursor is not None:
            params["cursor"] = str(cursor)

        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            # Minted fresh on every attempt, not once per page: the backoff schedule alone can
            # exceed the token's 60 s TTL within a single page's retries.
            headers = {
                "X-Internal-Token": mint_service_token(
                    self._settings,
                    audience=EMPLOYEE_AUDIENCE,
                    claims={"org": str(organization_id)},
                ),
                "X-Calling-Service": self._settings.service_name,
            }
            try:
                response = await self._client.get(
                    "/internal/v1/hierarchy", params=params, headers=headers
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                log.warning(
                    "employee.hierarchy.request_failed",
                    attempt=attempt + 1,
                    organization_id=str(organization_id),
                    error=str(exc)[:200],
                )
                if attempt < MAX_ATTEMPTS - 1:
                    await self._sleep(attempt)
                continue

            if response.status_code >= 500:
                last_error = httpx.HTTPStatusError(
                    f"{response.status_code} from employee-service",
                    request=response.request,
                    response=response,
                )
                log.warning(
                    "employee.hierarchy.upstream_5xx",
                    attempt=attempt + 1,
                    organization_id=str(organization_id),
                    status_code=response.status_code,
                )
                if attempt < MAX_ATTEMPTS - 1:
                    await self._sleep(attempt)
                continue

            if response.status_code >= 400:
                # A 401 means the audience, secret or issuer is wrong; a 422 means a malformed
                # cursor. Retrying either repeats the same mistake, so it fails on the first try.
                response.raise_for_status()

            body = response.json()
            return TeamPage(
                items=[_parse_item(item) for item in body["items"]],
                next_cursor=(
                    uuid.UUID(str(body["next_cursor"])) if body.get("next_cursor") else None
                ),
            )

        raise RetryExhaustedError(
            f"employee-service did not answer after {MAX_ATTEMPTS} attempts: {last_error}"
        ) from last_error

    async def _sleep(self, attempt: int) -> None:
        base = BACKOFF_BASE_SECONDS[min(attempt, len(BACKOFF_BASE_SECONDS) - 1)]
        # Full jitter over a retry backoff, not a security decision — `random` is the right tool.
        await asyncio.sleep(random.uniform(0, base))  # noqa: S311
