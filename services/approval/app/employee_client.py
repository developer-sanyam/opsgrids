"""The backfill's HTTP client — employee-service's one-time hierarchy export.

Talks to `GET /internal/v1/hierarchy` (ADR-0006 §2.1) over the service-token internal API, the
same discipline `subscription/app/catalog.py`'s `HttpPlanCatalog` follows for its one
cross-service read: a service-token client rather than a cross-schema read (ADR-0000 §10),
because employee's availability being a precondition of a one-time backfill is an acceptable
trade in a way it would not be on the request path.

Every number here is a decision from ADR-0006 §2.1's failure-contract table, not a default:

- **Page size 500.** 100 pages for the largest realistic tenant (50k employees).
- **Timeout 10 s total, 2 s connect.** A 500-row page is heavier than a single-row read.
- **Retry 5 attempts, backoff 1·2·4·8·16 s, full jitter** — on connection errors, timeouts and
  5xx **only**. A 4xx means the audience/secret or the cursor is wrong, and retrying it repeats
  the mistake.
- **Service token minted per HTTP request, not per page.** A 60 s token minted once at the start
  of a many-page run expires mid-run; `HttpPlanCatalog` mints per call for the same reason, and
  "per request" here includes retries, whose backoff alone can exceed 60 s.

This module owns exactly the HTTP call and its retry envelope. Pacing between pages, the
transaction per page, and resuming by cursor are `app/backfill.py`'s job — they are properties of
the run, not of one request.
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

log = get_logger("approval.employee_client")

#: ADR-0006 §2.1.
PAGE_SIZE = 500
TOTAL_TIMEOUT_SECONDS = 10.0
CONNECT_TIMEOUT_SECONDS = 2.0
MAX_ATTEMPTS = 5
#: Full jitter over 1, 2, 4, 8, 16 seconds.
BACKOFF_BASE_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0)

#: The audience employee-service's `require_service_token` checks against.
EMPLOYEE_AUDIENCE = "employee"


@dataclass(frozen=True, slots=True)
class HierarchyItem:
    """One row of `GET /internal/v1/hierarchy`, shaped like the event payload it mirrors
    (ADR-0006 §12): the same full-state fields, so `app/backfill.py` can feed both through the
    identical `upsert_hierarchy_node` call the consumer uses.
    """

    employee_id: uuid.UUID
    reporting_manager_id: uuid.UUID | None
    dotted_line_manager_id: uuid.UUID | None
    status: str
    display_name: str
    employee_no: str
    location_code: str | None
    #: `None` means an un-upgraded employee row — ADR-0006 §2.1 says it "is ignored, never
    #: treated as version 0", so `app/backfill.py` drops it rather than passing 0 to the upsert.
    hierarchy_version: int | None


@dataclass(frozen=True, slots=True)
class HierarchyPage:
    items: list[HierarchyItem]
    next_cursor: uuid.UUID | None


def _parse_item(raw: dict[str, Any]) -> HierarchyItem:
    return HierarchyItem(
        employee_id=uuid.UUID(str(raw["employee_id"])),
        reporting_manager_id=(
            uuid.UUID(str(raw["reporting_manager_id"])) if raw.get("reporting_manager_id") else None
        ),
        dotted_line_manager_id=(
            uuid.UUID(str(raw["dotted_line_manager_id"]))
            if raw.get("dotted_line_manager_id")
            else None
        ),
        status=str(raw["status"]),
        display_name=str(raw["display_name"]),
        employee_no=str(raw["employee_no"]),
        location_code=str(raw["location_code"]) if raw.get("location_code") else None,
        hierarchy_version=(
            int(raw["hierarchy_version"]) if raw.get("hierarchy_version") is not None else None
        ),
    )


class HierarchySource(Protocol):
    """What the backfill needs from employee-service. A test substitutes a stub."""

    async def fetch_page(
        self, organization_id: uuid.UUID, *, cursor: uuid.UUID | None, limit: int = PAGE_SIZE
    ) -> HierarchyPage: ...


class RetryExhaustedError(RuntimeError):
    """Every attempt failed with a retryable error — a connection problem, a timeout, or a 5xx."""


class HttpHierarchySource:
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
    ) -> HierarchyPage:
        """One page, retried per the table in the module docstring.

        A 4xx is never retried — raised on the first attempt, because retrying it repeats the
        same mistake (ADR-0006 §2.1). Connection errors, timeouts and 5xx get up to
        `MAX_ATTEMPTS` tries with full-jitter exponential backoff.
        """
        params: dict[str, str | int] = {"organization_id": str(organization_id), "limit": limit}
        if cursor is not None:
            params["cursor"] = str(cursor)

        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            # Minted fresh on every attempt, not once per page: the backoff schedule alone can
            # exceed the token's 60 s TTL within a single page's retries (module docstring).
            # `claims={"org": ...}` binds the organization being backfilled into the token
            # itself: employee's internal hierarchy route compares this against the
            # `organization_id` query parameter, so a captured or forged token addressed to
            # `employee` cannot be replayed against a *different* organization than the one it
            # was minted for, even though `iss=approval` alone would satisfy the audience check.
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
                # No point sleeping after the last attempt: the loop is about to end and raise
                # `RetryExhaustedError` below regardless, so a sleep here is up to 16 s spent
                # waiting to fail rather than backing off before a retry that will happen.
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
                # A 401 means the audience or secret is wrong; a 422 means a malformed cursor.
                # Retrying either repeats the same mistake, so this fails on the first attempt.
                response.raise_for_status()

            body = response.json()
            return HierarchyPage(
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
        # Full jitter over a retry backoff, not a security decision — `random` is the right
        # tool, not `secrets`.
        await asyncio.sleep(random.uniform(0, base))  # noqa: S311
