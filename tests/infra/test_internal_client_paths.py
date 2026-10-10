"""Every internal call the CRM-adjacent services make reaches a route its callee serves, names its
organization in its service token, and cannot be added without being registered here.

Why this file exists. `create_app` mounts every public router under `/api/v1` and every internal
router at the root (`libs/platform-core/src/platform_core/app.py:362-363`). The customer 360 asked
deal for `{deal_internal_url}/deals`, a path nobody serves, and its only test faked the upstream by
port, so the 404 was invisible: the `crm` section came back `null` and the failure was one log line.
This file is what keeps that class of defect from coming back unseen.

Three layers.

1. Each registered call is made by the REAL client function against an `httpx.MockTransport` that
   records the request, in a subprocess with the caller's service directory as cwd (`import app` is
   ambiguous across services, the same reason `test_route_table_parity.py` uses `_in_service`).
2. The recorded request is sent, without credentials, to the REAL callee app over ASGI. A route that
   exists answers 401 or 403 (authentication runs first); 404 means "not mounted there" and 405
   "wrong method".
3. The service token the client minted is decoded, and its `org` claim is compared with the
   organization the client was given.

A scan keeps the registry honest: every service file that builds an internal URL must be registered
in `CALLS` or named in `UNAUDITED_BASELINE`.

**Adding an internal client? Register its calls in `CALLS` in the same commit, wherever in
`services/` it lives.**
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES = REPO_ROOT / "services"

# --------------------------------------------------------------------------------- drivers

#: Runs first in every driver subprocess. Stable, valid UUIDs, so a recorded path can be sent to the
#: callee exactly as the client built it. `emit` prints the one recorded request as the last line.
_PRELUDE = """
import asyncio
import json
import uuid

import httpx
import jwt

ORG = "11111111-1111-4111-8111-111111111111"
USER = "33333333-3333-4333-8333-333333333333"
COMPANY = "44444444-4444-4444-8444-444444444444"
CONTACT = "22222222-2222-4222-8222-222222222222"
OWNER = "55555555-5555-4555-8555-555555555555"
DEAL = "66666666-6666-4666-8666-666666666666"
MEMBERSHIP = "77777777-7777-4777-8777-777777777777"

SEEN = []


def handler(request):
    SEEN.append(
        {
            "method": request.method,
            "path": request.url.path,
            "token": request.headers.get("X-Internal-Token"),
        }
    )
    return httpx.Response(200, json={})


def make_client():
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def attempt(awaitable):
    # Only the request matters here. Whether a client parses a stub answer is its own suite's job.
    try:
        await awaitable
    except Exception:
        pass


def emit(organization, settings=None, audience=None):
    (request,) = SEEN
    claims = None
    if request["token"] is not None:
        claims = jwt.decode(
            request["token"],
            settings.internal_service_token.get_secret_value(),
            algorithms=["HS256"],
            audience=audience,
        )
    print(
        json.dumps(
            {
                "method": request["method"],
                "path": request["path"],
                "claims": claims,
                "organization": organization,
            }
        )
    )
"""

# A1: customer -> deal. The caller's own identity is forwarded, so no service token is sent.
_A1 = """
from app import c1_360
from app.config import Settings
from platform_core.auth import Principal

org = uuid.UUID(ORG)
principal = Principal(sub=uuid.UUID(USER), organization_id=org, entitlements=["crm"])
caller = c1_360.CallerContext(
    organization_id=org, user_id=principal.sub, principal=principal, forward_headers={}
)


async def main():
    async with make_client() as client:
        await attempt(c1_360.fetch_crm(client, Settings(), caller, uuid.UUID(COMPANY)))


asyncio.run(main())
emit(ORG)
"""

# A2: customer -> notification. The path names no organization; the token's `org` claim does.
_A2 = """
from app import c1_360
from app.config import Settings
from platform_core.auth import Principal

org = uuid.UUID(ORG)
cfg = Settings()
principal = Principal(sub=uuid.UUID(USER), organization_id=org, entitlements=["engage"])
caller = c1_360.CallerContext(
    organization_id=org, user_id=principal.sub, principal=principal, forward_headers={}
)


async def main():
    async with make_client() as client:
        await attempt(
            c1_360.fetch_channels(client, cfg, caller, uuid.UUID(CONTACT), "jo@example.com")
        )


asyncio.run(main())
emit(ORG, cfg, "notification")
"""

# A3: lead -> customer `leads:convert`.
_A3 = """
from app import clients
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(
            clients.ensure_company_and_contact(
                cfg,
                uuid.UUID(ORG),
                company_id=uuid.UUID(COMPANY),
                contact_id=uuid.UUID(CONTACT),
                company_name="Acme Traders",
                owner_id=uuid.UUID(OWNER),
                contact_full_name="Jo Rivera",
                contact_email="jo@example.com",
                contact_raw_phone=None,
                client=client,
            )
        )


asyncio.run(main())
emit(ORG, cfg, "customer")
"""

# A4: lead -> deal `leads:convert`.
_A4 = """
from app import clients
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(
            clients.ensure_deal(
                cfg,
                uuid.UUID(ORG),
                deal_id=uuid.UUID(DEAL),
                company_id=uuid.UUID(COMPANY),
                contact_id=uuid.UUID(CONTACT),
                owner_id=uuid.UUID(OWNER),
                name="Acme Traders: first order",
                close_date="2026-12-01",
                source="form",
                client=client,
            )
        )


asyncio.run(main())
emit(ORG, cfg, "deal")
"""

# A5: lead -> customer `owners:validate`.
_A5 = """
from app import clients
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(
            clients.invalid_owner_ids(cfg, uuid.UUID(ORG), [uuid.UUID(OWNER)], client=client)
        )


asyncio.run(main())
emit(ORG, cfg, "customer")
"""

# A6: finance -> customer contact read.
_A6 = """
from app import clients
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(
            clients.fetch_crm_contact(cfg, uuid.UUID(ORG), uuid.UUID(CONTACT), client=client)
        )


asyncio.run(main())
emit(ORG, cfg, "customer")
"""

# A7: notification -> customer `messaging-target`.
_A7 = """
from app import targets
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(targets.resolve_contact_target(cfg, ORG, CONTACT, "whatsapp", client=client))


asyncio.run(main())
emit(ORG, cfg, "customer")
"""

# A8: notification -> customer `contacts:resolve`.
_A8 = """
from app import contacts
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(contacts.resolve_contact_by_phone(cfg, ORG, "+917976603446", client=client))


asyncio.run(main())
emit(ORG, cfg, "customer")
"""

# A9: notification -> account membership lookup.
_A9 = """
from app import memberships
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(memberships.membership_is_active(cfg, ORG, MEMBERSHIP, client=client))


asyncio.run(main())
emit(ORG, cfg, "account")
"""

# --------------------------------------------------------------------------------- the registry


class Call(NamedTuple):
    id: str
    caller: str  # the calling service's directory under services/
    callee: str  # the called service's directory; also the audience of its service token
    source: str  # the one file that builds the URL, relative to services/
    driver: str


CALLS: tuple[Call, ...] = (
    Call("A1", "customer", "deal", "customer/app/c1_360.py", _A1),
    Call("A2", "customer", "notification", "customer/app/c1_360.py", _A2),
    Call("A3", "lead", "customer", "lead/app/clients.py", _A3),
    Call("A4", "lead", "deal", "lead/app/clients.py", _A4),
    Call("A5", "lead", "customer", "lead/app/clients.py", _A5),
    Call("A6", "finance", "customer", "finance/app/clients.py", _A6),
    Call("A7", "notification", "customer", "notification/app/targets.py", _A7),
    Call("A8", "notification", "customer", "notification/app/contacts.py", _A8),
    Call("A9", "notification", "account", "notification/app/memberships.py", _A9),
)

#: Files that build an internal URL and were **not** audited (the audit read customer, deal, lead,
#: finance and notification). Unaudited is not clean: these may carry the path or claim defects the
#: five audited services had. The list may only shrink by registering a file's calls in `CALLS`, and
#: only grow on purpose, with the reason in the commit message.
UNAUDITED_BASELINE: frozenset[str] = frozenset(
    {
        # Build `/internal/v1/...` paths in place.
        "console/app/catalog_client.py",
        "console/app/delegation.py",
        "console/app/price.py",
        "console/app/subscription_client.py",
        "operations/app/orders/payment_client.py",
        # Hand a `*_service_url` to an `Http*` client defined elsewhere.
        "admin-gateway/app/main.py",
        "app-gateway/app/main.py",
        "approval/app/backfill.py",
        "auth/app/main.py",
        "billing/app/api/internal.py",
        "catalog/app/bundle_audit.py",
        "catalog/app/main.py",
        "subscription/app/backfill.py",
        "subscription/app/billing_run.py",
        "subscription/app/cron.py",
        "subscription/app/main.py",
        "subscription/app/worker.py",
    }
)

#: Defects this guard found on the base commit, each fixed by a named task of the plan, which
#: deletes its entry in the same commit as the fix. `strict=True` is the ratchet: once the fix
#: lands the test passes, the xfail becomes a failure, and the entry has to go.
KNOWN_UNREACHABLE: dict[str, str] = {}
KNOWN_UNCLAIMED: dict[str, str] = {}


def _params(calls: list[Call] | tuple[Call, ...], known: dict[str, str]) -> list[Any]:
    return [
        pytest.param(
            call,
            id=call.id,
            marks=pytest.mark.xfail(strict=True, reason=known[call.id]) if call.id in known else (),
        )
        for call in calls
    ]


# --------------------------------------------------------------------------------- the machinery


def _run(service: str, script: str, stdin: str | None = None) -> dict[str, Any]:
    """Run `script` with `services/<service>` as the import root; read the JSON on its last line."""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=SERVICES / service,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{service} subprocess failed:\n{result.stderr[-2000:]}"
    return dict(json.loads(result.stdout.strip().splitlines()[-1]))


@functools.cache
def _recorded(call_id: str) -> dict[str, Any]:
    call = next(c for c in CALLS if c.id == call_id)
    return _run(call.caller, _PRELUDE + call.driver)


_PROBE = """
import asyncio
import json
import sys

import httpx

from app.main import app

requests = json.loads(sys.stdin.read())


async def main():
    answers = {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://probe"
    ) as client:
        for key, (method, path) in requests.items():
            answers[key] = (await client.request(method, path)).status_code
    return answers


print(json.dumps(asyncio.run(main())))
"""


def _probe(callee: str, requests: dict[str, tuple[str, str]]) -> dict[str, int]:
    """Send each `(method, path)` to the callee's real app, over ASGI, with no credentials."""
    answers = _run(callee, _PROBE, json.dumps(requests))
    return {key: int(status) for key, status in answers.items()}


@functools.cache
def _probe_registered(callee: str) -> dict[str, int]:
    requests = {
        call.id: (_recorded(call.id)["method"], _recorded(call.id)["path"])
        for call in CALLS
        if call.callee == callee
    }
    return _probe(callee, requests)


# --------------------------------------------------------------------------------- the tests


def test_the_probe_tells_a_route_that_exists_from_one_that_does_not() -> None:
    """Without this, a probe that answered 401 to everything would pass every call below. These
    four are facts about the real deal app: the 360's old path is a 404, its new one is a route."""
    answers = _probe(
        "deal",
        {
            "old-360-path": ("GET", "/deals"),
            "mounted": ("GET", "/api/v1/deals"),
            "wrong-method": ("DELETE", "/api/v1/deals"),
            "nowhere": ("GET", "/api/v1/nowhere"),
        },
    )
    assert answers["old-360-path"] == 404
    assert answers["mounted"] not in (404, 405)
    assert answers["wrong-method"] == 405
    assert answers["nowhere"] == 404


@pytest.mark.parametrize("call", _params(CALLS, KNOWN_UNREACHABLE))
def test_every_registered_call_reaches_a_route_the_callee_serves(call: Call) -> None:
    recorded = _recorded(call.id)
    status = _probe_registered(call.callee)[call.id]
    assert status not in (404, 405), (
        f"{call.id}: {call.caller} -> {call.callee}: {recorded['method']} {recorded['path']} "
        f"answered {status} ({'not mounted there' if status == 404 else 'wrong method'}). "
        "Public routers are mounted under /api/v1, internal routers at the root "
        "(libs/platform-core/src/platform_core/app.py:362-363)."
    )


#: A1 forwards the caller's identity headers and sends no service token.
TOKEN_CALLS = [call for call in CALLS if call.id != "A1"]


@pytest.mark.parametrize("call", _params(TOKEN_CALLS, KNOWN_UNCLAIMED))
def test_every_registered_call_names_its_organization_in_its_token(call: Call) -> None:
    recorded = _recorded(call.id)
    claims = recorded["claims"]
    assert claims is not None, f"{call.id} sent no X-Internal-Token"
    assert claims["iss"] == call.caller
    assert claims["aud"] == call.callee
    in_path = re.search(r"/internal/v1/organizations/([^/]+)/", recorded["path"])
    expected = in_path.group(1) if in_path else recorded["organization"]
    assert expected == recorded["organization"], "the driver and the URL disagree"
    assert claims.get("org") == expected, (
        f"{call.id}: the token {call.caller} minted for {call.callee} carries "
        f"org={claims.get('org')!r}, not the organization {expected} it was asked about"
    )


def test_the_nine_audited_calls_stay_registered() -> None:
    assert {f"A{n}" for n in range(1, 10)} <= {call.id for call in CALLS}


_URL_SETTING = re.compile(r"_internal_url|_service_url")


def _files_building_internal_urls(services_root: Path) -> set[str]:
    found: set[str] = set()
    for path in sorted(services_root.glob("*/app/**/*.py")):
        relative = path.relative_to(services_root)
        if relative.parts[1:] == ("app", "config.py"):
            continue  # the settings that name the URLs are not a client
        if _URL_SETTING.search(path.read_text(encoding="utf-8")):
            found.add(relative.as_posix())
    return found


def _audit(services_root: Path) -> tuple[list[str], list[str]]:
    """(files that build an internal URL and are neither registered nor baselined, baselined
    files that no longer do)."""
    registered = {call.source for call in CALLS}
    found = _files_building_internal_urls(services_root)
    return sorted(found - registered - UNAUDITED_BASELINE), sorted(UNAUDITED_BASELINE - found)


def test_every_internal_url_in_these_services_is_a_registered_call() -> None:
    unknown, stale = _audit(SERVICES)
    assert not unknown, (
        f"{unknown} build an internal URL and are not registered: register the call in CALLS "
        "(a client), or name the file in UNAUDITED_BASELINE and say why in the commit message"
    )
    assert not stale, (
        f"{stale} no longer build an internal URL: remove them from UNAUDITED_BASELINE"
    )


def test_the_guard_flags_an_unregistered_file(tmp_path: Path) -> None:
    app = tmp_path / "billing" / "app"
    app.mkdir(parents=True)
    (app / "new_client.py").write_text("url = f'{settings.finance_internal_url}/internal/v1/x'\n")
    (app / "config.py").write_text("finance_internal_url = 'http://finance:8000'\n")
    (app / "quiet.py").write_text("print('no internal url here')\n")
    unknown, stale = _audit(tmp_path)
    assert unknown == ["billing/app/new_client.py"]
    assert stale == sorted(UNAUDITED_BASELINE)  # nothing in this tree matches the baseline
