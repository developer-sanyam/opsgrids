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
   exists answers 401 or 403 (authentication runs first); 404 means "not mounted there", 405
   "wrong method" and 307 a trailing slash. It must also be the registered request: the same method
   on a path that matches the registered route, because a call that drifts to another route the
   callee serves (`GET /api/v1/deals/summary`, `POST /api/v1/deals`) answers 401 just the same.
3. The service token the client minted is decoded, and its `org` claim is compared with the
   organization the client was given.

Two scans keep the registry honest. Every service file that builds an internal URL must be
registered in `CALLS` or named in `UNAUDITED_BASELINE`, which may not grow. And a registered file
reads no more internal URL settings than it has registered calls, so a new call in a file that is
already registered cannot slip in unseen.

The guard's own assertions are tested too: each callee's probe is shown to answer 404 for a route it
does not serve, and a deliberately bad call is shown to fail the path check and the claim check.

**Adding an internal client? Register its calls in `CALLS` in the same commit, wherever in
`services/` it lives.**
"""

from __future__ import annotations

import ast
import collections
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
ENQUIRY = "88888888-8888-4888-8888-888888888888"

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

# A10: notification -> storefront enquiry WhatsApp opt-in read.
_A10 = """
from app import storefront_optin
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(storefront_optin.fetch_storefront_opt_in(cfg, ORG, ENQUIRY, client=client))


asyncio.run(main())
emit(ORG, cfg, "storefront")
"""

# A12: customer -> deal `references`, the aborted-conversion cleanup's probe (CRM R2.4).
_A12 = """
from app import clients
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(
            clients.deal_references(
                cfg,
                uuid.UUID(ORG),
                company_id=uuid.UUID(COMPANY),
                contact_id=uuid.UUID(CONTACT),
                exclude_deal_id=uuid.UUID(DEAL),
                client=client,
            )
        )


asyncio.run(main())
emit(ORG, cfg, "deal")
"""

# --------------------------------------------------------------------------------- the registry


class Call(NamedTuple):
    id: str
    caller: str  # the calling service's directory under services/
    callee: str  # the called service's directory; also the audience of its service token
    method: str  # the HTTP method the call sends
    route: str  # the callee's route the call must reach, `{x}` standing for one path segment
    source: str  # the one file that builds the URL, relative to services/
    driver: str


CALLS: tuple[Call, ...] = (
    Call("A1", "customer", "deal", "GET", "/api/v1/deals", "customer/app/c1_360.py", _A1),
    Call(
        "A2",
        "customer",
        "notification",
        "GET",
        "/internal/v1/contacts/{contact}/messaging-summary",
        "customer/app/c1_360.py",
        _A2,
    ),
    Call(
        "A3",
        "lead",
        "customer",
        "POST",
        "/internal/v1/organizations/{org}/leads:convert",
        "lead/app/clients.py",
        _A3,
    ),
    Call(
        "A4",
        "lead",
        "deal",
        "POST",
        "/internal/v1/organizations/{org}/leads:convert",
        "lead/app/clients.py",
        _A4,
    ),
    Call(
        "A5",
        "lead",
        "customer",
        "POST",
        "/internal/v1/organizations/{org}/owners:validate",
        "lead/app/clients.py",
        _A5,
    ),
    Call(
        "A6",
        "finance",
        "customer",
        "GET",
        "/internal/v1/organizations/{org}/contacts/{contact}",
        "finance/app/clients.py",
        _A6,
    ),
    Call(
        "A7",
        "notification",
        "customer",
        "GET",
        "/internal/v1/organizations/{org}/contacts/{contact}/messaging-target",
        "notification/app/targets.py",
        _A7,
    ),
    Call(
        "A8",
        "notification",
        "customer",
        "POST",
        "/internal/v1/organizations/{org}/contacts:resolve",
        "notification/app/contacts.py",
        _A8,
    ),
    Call(
        "A9",
        "notification",
        "account",
        "GET",
        "/internal/v1/organizations/{org}/memberships/{membership}",
        "notification/app/memberships.py",
        _A9,
    ),
    Call(
        "A10",
        "notification",
        "storefront",
        "GET",
        "/internal/v1/enquiries/{enquiry}/whatsapp-opt-in",
        "notification/app/storefront_optin.py",
        _A10,
    ),
    Call(
        "A12",
        "customer",
        "deal",
        "GET",
        "/internal/v1/organizations/{org}/references",
        "customer/app/clients.py",
        _A12,
    ),
)

#: Files that build an internal URL and were **not** audited (the audit read customer, deal, lead,
#: finance and notification). Unaudited is not clean: these may carry the path or claim defects the
#: five audited services had. The list may only shrink, by registering a file's calls in `CALLS`;
#: `test_the_unaudited_baseline_only_shrinks` holds its size, so adding a file means raising that
#: number in the same diff, on purpose, with the reason in the commit message.
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


def _matches(route: str, path: str) -> bool:
    """Does `path`, as a client sent it, fill `route`, a template whose `{x}` is one segment?"""
    return re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", route), path) is not None


def _path_problem(call: Call, recorded: dict[str, Any], status: int) -> str | None:
    """Why the request a call made is not the registered one reaching a served route, or None.
    `status` is what the callee's real app answered that request with, given no credentials."""
    sent = f"{recorded['method']} {recorded['path']}"
    if recorded["method"] != call.method or not _matches(call.route, recorded["path"]):
        return f"sent {sent}, not the registered {call.method} {call.route}"
    if status not in (401, 403):
        why = {404: "not mounted there", 405: "wrong method"}.get(status, "not a served route")
        return (
            f"{sent} answered {status} ({why}). Public routers are mounted under /api/v1, "
            "internal routers at the root (libs/platform-core/src/platform_core/app.py:362-363)."
        )
    return None


def _claim_problem(call: Call, recorded: dict[str, Any]) -> str | None:
    """Why the token a call sent does not name the organization it was asked about, or None."""
    claims = recorded["claims"]
    if claims is None:
        return "sent no X-Internal-Token"
    if (claims.get("iss"), claims.get("aud")) != (call.caller, call.callee):
        return f"the token runs {claims.get('iss')} -> {claims.get('aud')}, not the registered pair"
    in_path = re.search(r"/internal/v1/organizations/([^/]+)/", recorded["path"])
    expected = in_path.group(1) if in_path else recorded["organization"]
    if expected != recorded["organization"]:
        return "the driver and the URL disagree"
    if claims.get("org") != expected:
        return f"the token carries org={claims.get('org')!r}, not the {expected} it was asked about"
    return None


# --------------------------------------------------------------------------------- the tests


def test_the_probe_tells_a_route_that_exists_from_one_that_does_not() -> None:
    """Without this, a probe that answered 401 to everything would pass every call below. These
    are facts about the real deal app: the 360's old path is a 404, its new one is a route, and the
    same path with a slash is a redirect (an httpx client follows none unless told to)."""
    answers = _probe(
        "deal",
        {
            "old-360-path": ("GET", "/deals"),
            "mounted": ("GET", "/api/v1/deals"),
            "trailing-slash": ("GET", "/api/v1/deals/"),
            "wrong-method": ("DELETE", "/api/v1/deals"),
            "nowhere": ("GET", "/api/v1/nowhere"),
        },
    )
    assert answers["old-360-path"] == 404
    assert answers["mounted"] in (401, 403)
    assert answers["trailing-slash"] not in (401, 403)
    assert answers["wrong-method"] == 405
    assert answers["nowhere"] == 404


@pytest.mark.parametrize("callee", sorted({call.callee for call in CALLS}))
def test_every_callee_answers_404_for_a_route_it_does_not_serve(callee: str) -> None:
    """The test above is deal only. A callee whose app rejected every anonymous request before
    routing, under either prefix, would answer a registered call 401 whether or not the route
    exists. Together with the 401 or 403 each registered call is held to below, a 404 here is what
    makes that answer mean "this route exists"."""
    answers = _probe(
        callee,
        {"public": ("GET", "/api/v1/nowhere-at-all"), "internal": ("GET", "/internal/v1/nowhere")},
    )
    assert answers == {"public": 404, "internal": 404}


@pytest.mark.parametrize("call", _params(CALLS, KNOWN_UNREACHABLE))
def test_every_registered_call_reaches_a_route_the_callee_serves(call: Call) -> None:
    problem = _path_problem(call, _recorded(call.id), _probe_registered(call.callee)[call.id])
    assert problem is None, f"{call.id}: {call.caller} -> {call.callee}: {problem}"


#: A1 forwards the caller's identity headers and sends no service token.
TOKEN_CALLS = [call for call in CALLS if call.id != "A1"]


@pytest.mark.parametrize("call", _params(TOKEN_CALLS, KNOWN_UNCLAIMED))
def test_every_registered_call_names_its_organization_in_its_token(call: Call) -> None:
    problem = _claim_problem(call, _recorded(call.id))
    assert problem is None, f"{call.id}: {call.caller} -> {call.callee}: {problem}"


# A client that asks deal for the 360's old path and is registered as it was written: the registry
# and the client agree, so only what deal answers can fail it.
_BAD_PATH = """
async def main():
    async with make_client() as client:
        await attempt(client.get("http://deal.invalid/deals"))


asyncio.run(main())
emit(ORG)
"""

# A client that mints its service token without the organization claim.
_BAD_CLAIM = """
from platform_core.internal_auth import mint_service_token
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        token = mint_service_token(cfg, audience="customer", caller=cfg.service_name)
        url = f"http://customer.invalid/internal/v1/organizations/{ORG}/contacts/{CONTACT}"
        await attempt(client.get(url, headers={"X-Internal-Token": token}))


asyncio.run(main())
emit(ORG, cfg, "customer")
"""


def test_the_path_check_fails_a_call_the_callee_does_not_serve() -> None:
    bad = Call("X1", "customer", "deal", "GET", "/deals", "customer/app/c1_360.py", _BAD_PATH)
    recorded = _run(bad.caller, _PRELUDE + bad.driver)
    status = _probe(bad.callee, {bad.id: (recorded["method"], recorded["path"])})[bad.id]
    assert status == 404
    assert _path_problem(bad, recorded, status) is not None


def test_the_path_check_fails_a_call_that_drifts_to_another_route_the_callee_serves() -> None:
    """Deal answers both of these 401, as it answers A1, so the status cannot tell them apart:
    only the registered method and route can."""
    a1 = next(call for call in CALLS if call.id == "A1")
    sent = {"another route": ("GET", "/api/v1/deals/summary"), "the other verb": ("POST", a1.route)}
    statuses = _probe("deal", sent)
    for key, (method, path) in sent.items():
        assert statuses[key] in (401, 403), key
        assert _path_problem(a1, {"method": method, "path": path}, statuses[key]) is not None, key


def test_the_claim_check_fails_a_token_with_no_organization() -> None:
    bad = Call(
        "X2",
        "notification",
        "customer",
        "GET",
        "/internal/v1/organizations/{org}/contacts/{contact}",
        "notification/app/targets.py",
        _BAD_CLAIM,
    )
    recorded = _run(bad.caller, _PRELUDE + bad.driver)
    assert "org" not in recorded["claims"]
    assert _claim_problem(bad, recorded) is not None


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


def test_the_unaudited_baseline_only_shrinks() -> None:
    # Lower this number when a file leaves the list; raising it is the decision to review.
    assert len(UNAUDITED_BASELINE) <= 17, "UNAUDITED_BASELINE grew: register the client instead"


def _url_reads(path: Path) -> int:
    """How many times a file reads an `*_internal_url` or `*_service_url` setting.

    shortcut: counts reads of the setting, not requests, so two calls built from one read count
    once; count requests instead if a client ever shares a base URL between calls."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        isinstance(node, ast.Attribute) and node.attr.endswith(("_internal_url", "_service_url"))
        for node in ast.walk(tree)
    )


def _read_count_drift(services_root: Path, calls: tuple[Call, ...] = CALLS) -> list[str]:
    """The registered files that read more, or fewer, internal URL settings than they have calls."""
    registered = collections.Counter(call.source for call in calls)
    return [
        f"{source}: {reads} internal URL reads, {count} registered calls"
        for source, count in sorted(registered.items())
        if (reads := _url_reads(services_root / source)) != count
    ]


def test_a_registered_file_reads_exactly_as_many_internal_urls_as_it_registers_calls() -> None:
    drift = _read_count_drift(SERVICES)
    assert not drift, f"{drift}: register the new call in CALLS, or drop the one that is gone"


def test_the_guard_flags_an_unregistered_file(tmp_path: Path) -> None:
    app = tmp_path / "billing" / "app"
    app.mkdir(parents=True)
    (app / "new_client.py").write_text("url = f'{settings.finance_internal_url}/internal/v1/x'\n")
    (app / "config.py").write_text("finance_internal_url = 'http://finance:8000'\n")
    (app / "quiet.py").write_text("print('no internal url here')\n")
    unknown, stale = _audit(tmp_path)
    assert unknown == ["billing/app/new_client.py"]
    assert stale == sorted(UNAUDITED_BASELINE)  # nothing in this tree matches the baseline


def test_the_guard_flags_a_new_call_in_a_registered_file(tmp_path: Path) -> None:
    client = tmp_path / "lead" / "app" / "clients.py"
    client.parent.mkdir(parents=True)
    client.write_text("url = f'{settings.customer_internal_url}/internal/v1/x'\n")
    registered = (
        Call("T1", "lead", "customer", "GET", "/internal/v1/x", "lead/app/clients.py", ""),
    )
    assert _read_count_drift(tmp_path, registered) == []

    client.write_text(client.read_text() + "url = f'{settings.deal_internal_url}/internal/v1/y'\n")
    assert _read_count_drift(tmp_path, registered) == [
        "lead/app/clients.py: 2 internal URL reads, 1 registered calls"
    ]
