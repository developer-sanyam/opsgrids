"""Boot the real platform, then let one ordered journey drive it over HTTP.

Every other suite in this repository tests one service with the others stubbed. That is why
outage-class defects keep reaching `main` with every suite green — each was a **contract
between two components**, and a stub encodes what the author believed the other side did.
Three arrived in a single day:

a. product-service's outbox relay ran as the RLS-bound app role. With no tenant bound its
   drain query matched zero rows, so it published nothing, logged nothing, and looped while
   the outbox filled. Activation "succeeded" and no permission was ever installed.
b. the gateway began minting signed, audience-bound service tokens while some services still
   expected the old static shared secret. Every permission lookup 403'd.
c. a product that spans two services shipped an admin role per service, and activation
   granted only the last one parsed — CRM's owners could read contacts and not deals.

None of the three is visible to a unit test, and all three are visible on the first real
request. So nothing here is stubbed: real processes, real Postgres, real RabbitMQ, real
tokens, real HTTP between every hop.

Three properties of this harness are deliberate.

**Free ports, never fixed ones.** An earlier version pinned 8100/8110/8111. A developer with
the stack already running had those ports taken, so the suite silently drove *that* stack —
against whatever database it was configured for — while seeding its own. Every assertion
failed for reasons that had nothing to do with the code under test. Ports are now allocated
from the OS and the gateway's route table is generated to match.

**The database, broker and cache are pinned by this file, not inherited.** `DATABASE_URL` is
passed explicitly to every subprocess so the suite cannot reach the shared development
database even if the developer's `.env` points there; the broker gets its own vhost so a
stray consumer elsewhere cannot eat this suite's events (and this suite's events can never be
delivered into another database); Redis gets its own numbered db.

**`ENVIRONMENT=ci`, not `local`.** It is the difference between a warning and a startup
failure for `assert_app_role` and `assert_outbox_role` — which is precisely defect (a). Under
`local` a relay handed an RLS-bound role logs a warning and stalls; under `ci` it refuses to
start, and this suite fails at boot with the reason instead of at an assertion ten steps later.

Services are subprocesses rather than in-process ASGI apps because every service exposes a
top-level `app` package and one process cannot import two of them. That constraint is why this
suite lives outside `services/` and drives everything over the network.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------- isolation

#: The worktree database. Pinned here and passed explicitly to every subprocess: the live
#: demo lives in `opsgrids`, and no `.env` on any developer's machine may steer this suite
#: into it.
#:
#: `E2E_DATABASE`, `E2E_REDIS_DB` and `E2E_VHOST` move the whole run to a second isolated set
#: — for a branch whose migrations must not land in the `opsgrids_wt` other worktrees share.
#: They are deliberately *not* `DATABASE_URL`: nothing in `.env` sets them, so the pin still
#: cannot be inherited by accident, and the shared `opsgrids` is refused outright.
_E2E_DATABASE = os.environ.get("E2E_DATABASE", "opsgrids_wt")
# A bare identifier and nothing else: `opsgrids?ssl=disable` or `opsgrids/x` would otherwise
# slip past a string comparison and still resolve to the live database.
if not re.fullmatch(r"[a-z][a-z0-9_]*", _E2E_DATABASE):
    raise RuntimeError(f"E2E_DATABASE must be a bare database name, got {_E2E_DATABASE!r}")
DATABASE_URL = f"postgresql+asyncpg://opsgrids:opsgrids@localhost:5434/{_E2E_DATABASE}"
# And judged on what the URL actually resolves to, not on what the variable said.
if make_url(DATABASE_URL).database == "opsgrids":
    raise RuntimeError("this suite must never run against the live `opsgrids` database")

#: Its own Redis db. The gateway keeps permission versions and rate-limit counters here, and
#: sharing db 0 with a running stack makes both flaky.
REDIS_URL = f"redis://localhost:6380/{int(os.environ.get('E2E_REDIS_DB', '9'))}"

#: Its own broker vhost, created on demand. Consumer queue names are compiled into the
#: services (`account.events`), so on the default vhost this suite and a running stack would
#: round-robin the same queue — and an event minted here could be consumed by a process
#: writing to the live database.
VHOST = os.environ.get("E2E_VHOST", "opsgrids_e2e")
if VHOST in ("", "/"):
    raise RuntimeError("E2E_VHOST must name a vhost of its own, never the default one")
RABBITMQ_URL = f"amqp://opsgrids:opsgrids@localhost:5673/{VHOST}"
RABBITMQ_MGMT = "http://localhost:15673"

PASSWORD = "e2e-Sufficiently-Long-Passw0rd!"

#: Every service these journeys need running. The gateway is last only for readability.
#:
#: `employee`, `attendance` and `approval` joined for the Wave 0 exit journey
#: (`test_approval_journey.py`): an attendance correction is raised in one service, routed by a
#: second off a hierarchy the third published, and applied back into the first. Every hop is a
#: contract between two services, which is the only kind of defect this suite exists to catch.
#: `notification` joined for `test_engage_journey.py`: Engage is a product this service serves
#: (a second catalogue, `app/catalogue.py::ENGAGE`, alongside the platform's own `notification`
#: plumbing), and the only way to prove its sending-identity API works for an authenticated
#: caller — rather than merely refusing one with no token — is to boot the real service and
#: drive it over HTTP.
#: `subscription` and `finance` joined for `test_bundle_journey.py`: a bundle is bought as a
#: subscription, and the trio it buys includes Finance — whose route has to answer 200 through
#: the gateway, not merely stop answering 402 against a port nothing listens on.
#: `billing`, `metering` and `payment` joined for `test_billing_run_journey.py`: a trial ends,
#: subscription's billing run asks billing for the first invoice (billing reads catalog and
#: metering to price it) and payment whether a mandate can collect it, and payment charges the
#: invoice when billing's `commerce.invoice.finalized` reaches it.
SERVICES = (
    "account",
    "product",
    "auth",
    "customer",
    "deal",
    "employee",
    "attendance",
    "approval",
    "notification",
    "catalog",
    "subscription",
    "finance",
    "billing",
    "metering",
    "payment",
    # `operations` joined for `test_inventory_journey.py`: lots, rolls, split and the ledger are
    # one service's own tables, but the journey proves the whole path a browser takes to them —
    # entitlement at the gateway, the OPERATIONS_ADMIN role the activation installs, then the
    # kernel's row locks and idempotent replay behind real HTTP.
    "operations",
    "app-gateway",
)

#: The committed route table addresses services by port. Rewriting by port rather than by
#: prefix keeps this harness in step with `routes.json` when a prefix is added or moved —
#: and `_generate_routes` fails loudly if a service it boots ends up with no route.
UPSTREAM_PORTS = {
    8110: "auth",
    8111: "account",
    8112: "product",
    8120: "employee",
    8121: "attendance",
    8130: "customer",
    8131: "deal",
    8151: "notification",
    8152: "approval",
    8160: "billing",
    8161: "subscription",
    8162: "metering",
    8164: "catalog",
    8171: "finance",
    8172: "operations",
}

#: The background processes that carry an event from the service that staged it to the service
#: that acts on it: `(process name, service directory, module)`. Every one of them is as much
#: part of the platform as an API — the journeys' central steps are events crossing between
#: services — so they boot with everything else rather than being started mid-test.
#:
#: `attendance` and `approval` each run **both** a relay and a consumer, because the
#: regularization round trip crosses between them twice: attendance stages
#: `hrms.time.regularization.requested` and approval consumes it; approval stages
#: `core.approval.request.decided` and attendance consumes it; attendance then stages
#: `hrms.time.regularization.applied` and approval consumes that. Drop any one of these
#: processes and the journey stalls at a different step, which is exactly what makes them worth
#: running for real.
#:
#: `auth-relay`, `notification-relay` and `notification-worker` joined for C8: every tenant
#: this suite seeds now signs up over HTTP and proves its address the way a real signup does.
#: The round trip is three hops, each needing its own drain — auth-service stages
#: `core.user.password_reset_requested` on its own outbox (`auth-relay` publishes it);
#: notification's fold consumer (half of `notification-worker`) turns that into a `pending`
#: `messages` row and stages `notification.delivery.message.queued` on notification's **own**
#: outbox (`notification-relay` publishes that in turn); only then does the dispatcher (the
#: other half of `notification-worker`) pick it up and mark the message `sent`, which is what
#: `_read_reset_email.py` polls for. Missing any one of the three leaves the message sitting
#: `pending` forever, exactly as a missing relay leaves any other outbox filling silently.
#:
#: The bundle journey adds the commerce loop: subscription's relay publishes the
#: `commerce.subscription.*` event, product's consumer projects each member's activation, and
#: account's consumer (already here) installs each member's manifest.
BACKGROUND = (
    ("subscription-relay", "subscription", "app.relay"),
    ("product-consumer", "product", "app.consumers"),
    ("product-relay", "product", "app.relay"),
    ("account-consumer", "account", "app.consumers"),
    ("auth-relay", "auth", "app.relay"),
    ("notification-relay", "notification", "app.relay"),
    ("notification-worker", "notification", "app.worker"),
    ("employee-relay", "employee", "app.relay"),
    ("attendance-relay", "attendance", "app.relay"),
    ("attendance-consumer", "attendance", "app.consumers"),
    ("approval-relay", "approval", "app.relay"),
    ("approval-consumer", "approval", "app.consumers"),
    # The billing run's money hop: billing stages `commerce.invoice.finalized`, its relay
    # publishes it, payment's consumer raises the charge against the (stubbed) provider.
    ("billing-relay", "billing", "app.relay"),
    ("payment-consumer", "payment", "app.consumers"),
)

#: Booted services with no route in `routes.json`, on purpose. payment has no customer surface —
#: a person never creates a charge — so the product gateway routes nothing to it; the billing run
#: reaches it service to service, and its webhook is admin-gateway's.
UNROUTED = frozenset({"payment"})

#: The signing key for the whole run. One value shared by every process, which is what
#: `internal_auth` expects; the gateway and each service derive per-call tokens from it.
INTERNAL_TOKEN = "dev-only-internal-token-padded-to-32-bytes-minimum"


def _free_port() -> int:
    """A port the OS says is free right now.

    Bound to port 0 and released immediately. There is a race between releasing and the
    service binding, but it is small and the alternative — fixed ports — is the defect this
    replaced.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class Platform:
    """The running stack: where everything is, and what it wrote."""

    ports: dict[str, int]
    log_dir: Path
    processes: list[tuple[str, subprocess.Popen[bytes]]] = field(default_factory=list)

    @property
    def gateway(self) -> str:
        """`localhost`, deliberately — **not** the loopback address.

        The gateway infers a tenant slug from the Host header (`acme.opsgrids.com` → `acme`),
        skipping hosts with fewer than three dotted labels. `127.0.0.1` has four, so an IP
        address is read as the organization `127`: sign-in then finds no membership matching
        that slug and offers an organization picker instead of a token. `localhost` is one
        label, so no slug is inferred and the journey signs in the way a browser does.
        """
        return f"http://localhost:{self.ports['app-gateway']}"

    def url(self, service: str) -> str:
        return f"http://127.0.0.1:{self.ports[service]}"

    def logs(self, service: str, tail: int = 3000) -> str:
        path = self.log_dir / f"{service}.log"
        if not path.exists():
            return f"(no log for {service})"
        return path.read_text(errors="replace")[-tail:]


def _ensure_infrastructure() -> None:
    """Fail with the fix rather than a connection error ten seconds into boot."""
    for name, host, port in (
        ("postgres", "localhost", 5434),
        ("redis", "localhost", 6380),
        ("rabbitmq", "localhost", 5673),
    ):
        try:
            with socket.create_connection((host, port), timeout=2):
                pass
        except OSError as exc:
            pytest.fail(f"{name} is not reachable on {host}:{port} ({exc}). Run `make infra`.")

    # The vhost is created rather than assumed: a fresh machine has only "/", and a missing
    # vhost surfaces as an opaque AMQP handshake failure inside the relay.
    try:
        auth = ("opsgrids", "opsgrids")
        httpx.put(f"{RABBITMQ_MGMT}/api/vhosts/{VHOST}", auth=auth, timeout=10.0)
        httpx.put(
            f"{RABBITMQ_MGMT}/api/permissions/{VHOST}/opsgrids",
            auth=auth,
            json={"configure": ".*", "write": ".*", "read": ".*"},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        pytest.fail(f"could not provision the {VHOST!r} broker vhost via {RABBITMQ_MGMT}: {exc}")


def _generate_routes(ports: dict[str, int], target: Path) -> None:
    """The committed route table with this run's ports substituted in.

    Routes whose upstream this suite does not boot are left pointing where they were, which is
    harmless: nothing answers on them, so a request that reached one would fail loudly rather
    than quietly pass.

    The unentitled-product step (`/api/v1/employees` for a tenant that never bought HRMS) is
    stronger than it looks now that employee-service *is* booted: the gateway refuses with 402
    before it proxies, and if that gate ever regressed to proxying first the request would now
    reach a live service and come back 200/403 — a failed assertion either way, but for the
    right reason rather than because nothing was listening.
    """
    table = json.loads((ROOT / "services" / "app-gateway" / "routes.json").read_text())
    routed: set[str] = set()
    for route in table["routes"]:
        port = int(route["upstream"].rsplit(":", 1)[1])
        service = UPSTREAM_PORTS.get(port)
        if service is not None:
            route["upstream"] = f"http://127.0.0.1:{ports[service]}"
            routed.add(service)

    missing = {s for s in SERVICES if s != "app-gateway" and s not in UNROUTED} - routed
    if missing:
        raise RuntimeError(
            f"no route in routes.json points at {sorted(missing)} — UPSTREAM_PORTS in this "
            "file is keyed by the committed table's ports and has drifted from it"
        )
    target.write_text(json.dumps(table, indent=2))


def _base_env(ports: dict[str, int], routes_file: Path) -> dict[str, str]:
    """What every subprocess gets. Explicit, so nothing is inherited by accident."""
    return {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        # `ci` rather than `local`: makes the RLS role assertions fatal instead of advisory.
        "ENVIRONMENT": "ci",
        "DEBUG": "false",
        "LOG_JSON": "false",
        "LOG_LEVEL": "INFO",
        "DATABASE_URL": DATABASE_URL,
        "REDIS_URL": REDIS_URL,
        "RABBITMQ_URL": RABBITMQ_URL,
        "INTERNAL_SERVICE_TOKEN": INTERNAL_TOKEN,
        # Absolute: every process runs from its own service directory, and the committed
        # `.env` carries this as a path relative to the repository root.
        "JWT_PRIVATE_KEY_PATH": str(ROOT / "tools" / "dev-bootstrap" / "keys" / "dev-ed25519.pem"),
        "JWKS_URL": f"http://127.0.0.1:{ports['auth']}/.well-known/jwks.json",
        "ACCOUNT_SERVICE_URL": f"http://127.0.0.1:{ports['account']}",
        "PRODUCT_SERVICE_URL": f"http://127.0.0.1:{ports['product']}",
        # subscription resolves the pin (and a bundle's members) from catalog's internal read.
        "CATALOG_SERVICE_URL": f"http://127.0.0.1:{ports['catalog']}",
        # The billing run: subscription calls billing and payment; billing reads catalog (above)
        # and metering to price a period.
        "BILLING_SERVICE_URL": f"http://127.0.0.1:{ports['billing']}",
        "PAYMENT_SERVICE_URL": f"http://127.0.0.1:{ports['payment']}",
        "METERING_SERVICE_URL": f"http://127.0.0.1:{ports['metering']}",
        # approval-service's hierarchy backfill reads employee-service's internal export
        # (ADR-0006 §2.1). The Wave 0 journey drives the hierarchy through the *event* stream
        # rather than the backfill, but the setting is part of the service's configuration and
        # pointing it at a stale default would be a trap for whoever adds the backfill step.
        "EMPLOYEE_SERVICE_URL": f"http://127.0.0.1:{ports['employee']}",
        "PERMISSION_MANIFEST_DIR": str(ROOT / "libs" / "contracts" / "permissions"),
        "ROUTES_FILE": str(routes_file),
    }


def _spawn(
    name: str, args: list[str], cwd: Path, env: dict[str, str], log_dir: Path
) -> subprocess.Popen[bytes]:
    """Start one process, its output going to a file.

    A file rather than `subprocess.PIPE`: eight processes logging into pipes nobody reads
    fill their buffers and block, and the service that stops writing looks like a hang.
    """
    handle = (log_dir / f"{name}.log").open("wb")
    return subprocess.Popen(args, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT)


def _wait_until_ready(
    platform: Platform, service: str, proc: subprocess.Popen[bytes], timeout: float = 90.0
) -> None:
    """Poll readiness rather than sleeping a guessed interval.

    A fixed sleep is either slower than it needs to be or flaky on a loaded machine, and the
    failure it produces — connection refused halfway through a journey — reads as a product
    bug rather than a startup race.
    """
    port = platform.ports[service]
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            # It exited. Its own output says why; a bare timeout would not, and the first
            # version of this harness hid a real configuration bug behind "connection refused".
            raise RuntimeError(
                f"{service} exited with code {proc.returncode} before becoming ready:\n"
                f"{platform.logs(service)}"
            )
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/health/ready", timeout=2.0)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return
            last = response.text[:200]
        except httpx.HTTPError as exc:
            last = str(exc)[:200]
        time.sleep(0.25)

    raise RuntimeError(
        f"{service} was not ready on :{port} within {timeout}s — last: {last}\n"
        f"{platform.logs(service)}"
    )


def _seed(script: str, service: str, *args: str, extra_env: dict[str, str] | None = None) -> str:
    """Run a seeder inside its own service directory.

    Its own process for the same reason the services are: one interpreter can hold only one
    service's `app`. Uses this interpreter rather than `uv run` — the environment is already
    resolved, and eight `uv run` startups is most of a minute.
    """
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / script),
            str(ROOT / "services" / service),
            *args,
        ],
        cwd=ROOT / "services" / service,
        env={
            **os.environ,
            "DATABASE_URL": DATABASE_URL,
            "REDIS_URL": REDIS_URL,
            "RABBITMQ_URL": RABBITMQ_URL,
            # `ci`, matching the services this suite boots: `assert_app_role` and
            # `assert_outbox_role` refuse rather than warn, so a seeder or the sweep runner that
            # connected on the wrong role fails here with the reason instead of silently seeing
            # no rows — the same reasoning the module docstring gives for the services.
            "ENVIRONMENT": "ci",
            **(extra_env or {}),
        },
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{script} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


@pytest.fixture(scope="session")
def platform(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """The whole stack: every service named in `SERVICES`, and every relay and consumer named
    in `BACKGROUND`.

    The relays and consumers are as much a part of the platform as the APIs — every journey's
    central step is an event crossing between two services — so they boot with everything
    else rather than being started halfway through a test.
    """
    _ensure_infrastructure()

    log_dir = tmp_path_factory.mktemp("e2e-logs")
    ports = {name: _free_port() for name in SERVICES}
    routes_file = log_dir / "routes.json"
    _generate_routes(ports, routes_file)

    stack = Platform(ports=ports, log_dir=log_dir)
    env = _base_env(ports, routes_file)

    try:
        for name in SERVICES:
            proc = _spawn(
                name,
                [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(ports[name])],
                ROOT / "services" / name,
                env,
                log_dir,
            )
            stack.processes.append((name, proc))

        for name, proc in list(stack.processes):
            _wait_until_ready(stack, name, proc)

        # The background processes the journeys' event steps depend on. product-service stages
        # `core.product.activated`; its relay publishes it; account-service's consumer installs
        # the manifest. Defect (a) killed that middle hop silently. `BACKGROUND` names the rest.
        for name, service, module in BACKGROUND:
            proc = _spawn(
                name, [sys.executable, "-m", module], ROOT / "services" / service, env, log_dir
            )
            stack.processes.append((name, proc))

        # None of them serves HTTP, so readiness is "still alive a moment later" — enough to
        # catch the RLS-role refusal that `ENVIRONMENT=ci` now turns into an exit.
        time.sleep(3.0)
        for name, _, _ in BACKGROUND:
            proc = next(p for n, p in stack.processes if n == name)
            if proc.poll() is not None:
                raise RuntimeError(
                    f"{name} exited with code {proc.returncode} at startup:\n{stack.logs(name)}"
                )

        yield stack
    finally:
        for _, proc in stack.processes:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
        for name, proc in stack.processes:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                print(f"{name} did not stop on SIGINT and was killed", file=sys.stderr)


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _signup(http: httpx.Client, *, org_name: str, slug: str, full_name: str, email: str) -> str:
    """`POST /api/v1/account/signup`. Returns the new organization's id.

    The real route (ADR-0013 §3): a stranger with no account and no token creates an
    organization and becomes its owner. Setting `is_owner` — the column `add_member` alone
    cannot touch — is `services.signup`'s job, so an owner created this way is a real owner for
    every purpose the CRM journey's activation step depends on, with no separate repair step.
    """
    response = http.post(
        "/api/v1/account/signup",
        json={"organization_name": org_name, "slug": slug, "full_name": full_name, "email": email},
    )
    if response.status_code != 201:
        raise RuntimeError(
            f"signup for {slug!r} failed: {response.status_code} {response.text[:400]}"
        )
    return str(response.json()["organization_id"])


def _request_reset(http: httpx.Client, email: str) -> None:
    forgot = http.post("/api/v1/auth/password/forgot", json={"email": email})
    if forgot.status_code != 202:
        raise RuntimeError(
            f"password/forgot for {email!r} failed: {forgot.status_code} {forgot.text[:400]}"
        )


def _confirm_reset(http: httpx.Client, email: str, password: str) -> None:
    token = json.loads(_seed("_read_reset_email.py", "notification", email))["token"]
    if not token:
        raise RuntimeError(
            f"no set-password email ever arrived for {email!r} — check auth-relay and "
            "notification-worker are up in the platform's own logs"
        )

    reset = http.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": password}
    )
    if reset.status_code != 204:
        raise RuntimeError(
            f"password/reset for {email!r} failed: {reset.status_code} {reset.text[:400]}"
        )


def _set_password_via_reset(http: httpx.Client, email: str, password: str) -> None:
    """Prove the address and set its first password — the only way a signed-up owner (or an
    accepted invitee) ever gets one: `services.signup`/`accept_invitation` deliberately create
    no credential (C8). Reads the link off the real delivery path (`_read_reset_email.py`),
    exactly as a person reading their own inbox would, rather than reaching into `auth`'s
    `credentials` table directly.
    """
    _request_reset(http, email)
    _confirm_reset(http, email, password)


def _login(http: httpx.Client, email: str, password: str, org_id: str) -> str:
    """Sign in through the gateway, following the pre-auth → select-org hop a person belonging
    to more than one organization takes. Every tenant this fixture creates has exactly one
    membership at this point, so the direct-token branch is the one that actually runs; the
    picker branch exists because `test_journey.py::login` is not importable here (it, in turn,
    depends on fixtures this module defines) and the two must not silently drift apart.
    """
    response = http.post("/api/v1/auth/login", json={"email": email, "password": password})
    if response.status_code != 200:
        raise RuntimeError(
            f"sign-in as {email!r} failed: {response.status_code} {response.text[:400]}"
        )
    body = response.json()
    if body.get("access_token"):
        return str(body["access_token"])
    if body.get("status") != "org_selection_required":
        raise RuntimeError(f"sign-in as {email!r} returned neither a token nor a picker: {body}")
    chosen = http.post(
        "/api/v1/auth/select-org",
        json={"pre_auth_token": body["pre_auth_token"], "organization_id": org_id},
    )
    if chosen.status_code != 200:
        raise RuntimeError(
            f"selecting org for {email!r} failed: {chosen.status_code} {chosen.text[:400]}"
        )
    return str(chosen.json()["access_token"])


def _find_member(http: httpx.Client, token: str, email: str) -> dict[str, Any]:
    """The membership id and user id HTTP never hands back from signup itself (`SignupOut`
    carries neither) — read back the same way any other client would have to: list the
    organization's own members and match on the address this fixture just created.
    """
    response = http.get("/api/v1/members", headers=_auth_header(token), params={"size": 200})
    if response.status_code != 200:
        raise RuntimeError(f"listing members failed: {response.status_code} {response.text[:400]}")
    for item in response.json()["items"]:
        if item["email"] == email:
            return {"user_id": item["user_id"], "membership_id": item["id"], "email": email}
    raise RuntimeError(f"{email!r} did not appear in its own organization's member list")


@pytest.fixture(scope="session")
def tenants(platform: Platform) -> dict[str, Any]:
    """Two organizations, each with an owner, and CRM data in both — created entirely over
    HTTP (C8), the way a real customer creates one.

    Two rather than one because "another tenant's data" is not assertable against a single
    tenant, and a cross-tenant test whose victim row does not exist passes for the wrong
    reason. `POST /api/v1/account/signup` is the real route (ADR-0013 §3): there is now an
    HTTP path that creates an organization, and this suite uses it instead of writing
    `account`'s schema directly. Every slug and every address carries this run's own suffix, so
    two runs against the same database never collide and nothing here needs to `TRUNCATE`
    anything to start clean.

    Alpha carries both an owner and an administrator, because the split between them is
    load-bearing and tested in both directions: the owner activates a product, the
    administrator is refused. The administrator is invited and accepts entirely over HTTP —
    the same path `TestAnInvitationIsAcceptedOverHttp` proves works for a person the journey
    invites later at runtime — rather than being seeded as a second owner-shaped row. Beta
    needs only an owner; it exists to be read *at*.
    """
    run = uuid.uuid4().hex[:8]
    seeded: dict[str, Any] = {}

    with httpx.Client(base_url=platform.gateway, timeout=30.0) as http:
        for org_key, name in (("alpha", "Alpha Anvils"), ("beta", "Beta Bearings")):
            slug = f"e2e-{org_key}-{run}"
            owner_email = f"e2e-{org_key}-owner-{run}@example.com"
            org_id = _signup(
                http, org_name=name, slug=slug, full_name=f"{name} Owner", email=owner_email
            )
            _set_password_via_reset(http, owner_email, PASSWORD)
            owner_token = _login(http, owner_email, PASSWORD, org_id)
            owner = {**_find_member(http, owner_token, owner_email), "role_code": "ORG_OWNER"}

            entry: dict[str, Any] = {
                "org_id": org_id,
                "org_slug": slug,
                "name": name,
                "owner": owner,
            }

            if org_key == "alpha":
                admin_email = f"e2e-{org_key}-admin-{run}@example.com"
                invited = http.post(
                    "/api/v1/invitations",
                    headers=_auth_header(owner_token),
                    json={"email": admin_email, "role_codes": ["ORG_ADMIN"], "product_codes": []},
                )
                if invited.status_code != 201:
                    raise RuntimeError(
                        f"inviting the alpha admin failed: {invited.status_code} "
                        f"{invited.text[:400]}"
                    )
                accepted = http.post(
                    "/api/v1/invitations/accept",
                    json={"token": invited.json()["token"], "full_name": f"{name} Admin"},
                )
                if accepted.status_code not in (200, 201):
                    raise RuntimeError(
                        f"accepting the alpha admin invite failed: {accepted.status_code} "
                        f"{accepted.text[:400]}"
                    )
                member = accepted.json()
                _set_password_via_reset(http, admin_email, PASSWORD)
                entry["admin"] = {
                    "user_id": member["user_id"],
                    "membership_id": member["id"],
                    "email": admin_email,
                    "role_code": "ORG_ADMIN",
                }

            seeded[org_key] = entry

    # product-service's registry, and the CRM rows the entitled steps read back. Neither has an
    # HTTP creation path that fits this suite's needs (see `_seed_product.py`/`_seed_crm.py`),
    # so these two stay direct-DB seeders.
    _seed("_seed_product.py", "product")
    for index, org in enumerate(("alpha", "beta")):
        owner = seeded[org]["owner"]
        _seed(
            "_seed_crm.py",
            "customer",
            seeded[org]["org_id"],
            owner["user_id"],
            owner["membership_id"],
            org,
            "reset" if index == 0 else "keep",
        )

    return {**seeded, "password": PASSWORD}


@pytest.fixture(scope="session")
def journey() -> dict[str, Any]:
    """State carried between the ordered steps — tokens, ids, what the last step proved."""
    return {}


@pytest.fixture(scope="session")
def probe(platform: Platform) -> Any:
    """Read the outbox and the consumer's ledger directly, to say *which* hop broke.

    `product_key` is required on the `"product"` side: `tenants` is a session fixture, and a
    full run activates more than one product for the same organization (engage, then crm, both
    on alpha). The outbox holds one `core.product.activated` row per activation, all sharing
    that one event type — a probe that only matched on `organization_id` used to report
    "published" the instant *any* of them had gone out, so a caller waiting on its own,
    not-yet-published activation would see an earlier file's already-published one and stop
    waiting before its own event had even reached the relay. See `_probe.py` for the rest of
    the story, including why the `"account"` side cannot be scoped the same way.
    """

    def _read(service: str, org_id: str, product_key: str | None = None) -> dict[str, Any]:
        target = "product" if service == "product" else "account"
        # `_probe.py`'s fourth positional argument is an event-type pattern, not a product
        # key — `event_probe` below uses that slot to watch a different event type entirely.
        # Every caller of `probe` is watching `core.product.activated`, so that is passed
        # explicitly rather than left to the script's own default, and `product_key` — when
        # given — rides as the fifth argument.
        args = [org_id, "core.product.activated"]
        if product_key is not None:
            args.append(product_key)
        raw = _seed("_probe.py", target, target, *args)
        return dict(json.loads(raw.splitlines()[-1]))

    return _read


@pytest.fixture(scope="session")
def event_probe(platform: Platform) -> Any:
    """`probe`, for any service's outbox (`which="product"`) or consumer ledger
    (`which="account"`), filtered by an event-type `LIKE` pattern — so the bundle journey can
    watch `commerce.subscription.%` leave subscription-service and land in account-service."""

    def _read(service: str, which: str, org_id: str, pattern: str) -> dict[str, Any]:
        raw = _seed("_probe.py", service, which, org_id, pattern)
        return dict(json.loads(raw.splitlines()[-1]))

    return _read


@pytest.fixture(scope="session")
def operator_call(platform: Platform) -> Any:
    """POST to a service's `/internal/v1` API with a service token, as an operator surface would.

    Bundles are sold only through subscription's internal operator routes (the customer route
    refuses them), and no operator console in this harness delegates yet — so the journey makes
    the delegated call itself, with a token minted from the run's shared signing key and
    addressed to the service by name, exactly as the gateway mints its own.
    """
    from platform_core.config import ServiceSettings
    from platform_core.internal_auth import mint_service_token

    caller = ServiceSettings(
        service_name="e2e-operator", db_schema="public", internal_service_token=INTERNAL_TOKEN
    )

    def _post(service: str, path: str, body: dict[str, Any]) -> httpx.Response:
        token = mint_service_token(caller, audience=service)
        return httpx.post(
            f"{platform.url(service)}{path}",
            json=body,
            headers={"X-Internal-Token": token, "X-Calling-Service": "e2e-operator"},
            timeout=15.0,
        )

    return _post


@pytest.fixture(scope="session")
def internal_get(platform: Platform) -> Any:
    """GET a service's `/internal/v1` API with a service token, like `operator_call` but a read.

    The customer catalogue read no longer carries prices (they are set per client by staff), so
    a journey that needs the amount a plan version costs reads it here, the way subscription
    and console do.
    """
    from platform_core.config import ServiceSettings
    from platform_core.internal_auth import mint_service_token

    caller = ServiceSettings(
        service_name="e2e-operator", db_schema="public", internal_service_token=INTERNAL_TOKEN
    )

    def _get(service: str, path: str) -> httpx.Response:
        token = mint_service_token(caller, audience=service)
        return httpx.get(
            f"{platform.url(service)}{path}",
            headers={"X-Internal-Token": token, "X-Calling-Service": "e2e-operator"},
            timeout=15.0,
        )

    return _get


@pytest.fixture(scope="session")
def bundle_tenant(platform: Platform, tenants: dict[str, Any]) -> dict[str, Any]:
    """A fresh organization for the bundle journey, with a signed-in owner and nothing bought —
    over HTTP (C8), exactly like `tenants` and `approval_tenant`.

    Its own tenant, like `approval_tenant`, because the journey's first assertion is a 402 on
    every trio member — which alpha (CRM) and the approval tenant (HRMS) would each fail for a
    reason unrelated to bundles. Depends on `tenants` for ordering only.
    """
    run = f"bundle-{uuid.uuid4().hex[:8]}"
    slug = f"e2e-{run}"
    email = f"e2e-{run}-owner@example.com"
    with httpx.Client(base_url=platform.gateway, timeout=30.0) as http:
        org_id = _signup(
            http, org_name="Delta Drives", slug=slug, full_name="Delta Drives Owner", email=email
        )
        _set_password_via_reset(http, email, PASSWORD)
        token = _login(http, email, PASSWORD, org_id)
        owner = _find_member(http, token, email)

    return {
        "org_id": org_id,
        "org_slug": slug,
        "owner": owner,
        "run": run,
        "password": PASSWORD,
    }


@pytest.fixture(scope="session")
def billing_tenant(platform: Platform, tenants: dict[str, Any]) -> dict[str, Any]:
    """A fresh organization for the billing-run journey: a signed-in owner, nothing bought.

    Its own tenant for the bundle tenant's reason — the journey's invoice and charge counts must
    be this organization's alone. Depends on `tenants` for ordering only.
    """
    run = f"billing-{uuid.uuid4().hex[:8]}"
    slug = f"e2e-{run}"
    email = f"e2e-{run}-owner@example.com"
    with httpx.Client(base_url=platform.gateway, timeout=30.0) as http:
        org_id = _signup(
            http, org_name="Epsilon Exports", slug=slug, full_name="Epsilon Owner", email=email
        )
        _set_password_via_reset(http, email, PASSWORD)
        token = _login(http, email, PASSWORD, org_id)
        owner = _find_member(http, token, email)
    return {"org_id": org_id, "org_slug": slug, "owner": owner, "run": run, "password": PASSWORD}


@pytest.fixture(scope="session")
def billing_run(platform: Platform) -> Any:
    """One pass of subscription's real billing run, `seconds_ahead` past PostgreSQL's `now()`,
    calling the running billing and payment services with the run's shared signing key — with a
    billing cutover, as production must have one (`BILLING_RUN_NOT_BEFORE`): the moment this
    fixture is first used. The journey's trial ends after it, so it is invoiced as before."""
    from datetime import UTC, datetime

    cutover = datetime.now(UTC).isoformat()

    def _tick(seconds_ahead: int) -> dict[str, Any]:
        raw = _seed(
            "_billing_run.py",
            "subscription",
            str(seconds_ahead),
            extra_env={
                "BILLING_SERVICE_URL": platform.url("billing"),
                "PAYMENT_SERVICE_URL": platform.url("payment"),
                "INTERNAL_SERVICE_TOKEN": INTERNAL_TOKEN,
                "BILLING_RUN_NOT_BEFORE": cutover,
            },
        )
        return dict(json.loads(raw.splitlines()[-1]))

    return _tick


@pytest.fixture(scope="session")
def seed_mandate(platform: Platform) -> Any:
    """An active, bank-confirmed mandate for one organization (payment has no route for it yet)."""

    def _seed_one(org_id: str, currency: str, cap_minor: int) -> str:
        raw = _seed("_seed_mandate.py", "payment", org_id, currency, str(cap_minor))
        return str(json.loads(raw.splitlines()[-1])["mandate_id"])

    return _seed_one


@pytest.fixture(scope="session")
def commerce_probe(platform: Platform) -> Any:
    """billing's invoices or payment's charges for one organization, read off the database."""

    def _read(which: str, org_id: str) -> dict[str, Any]:
        raw = _seed("_commerce_probe.py", which, which, org_id)
        return dict(json.loads(raw.splitlines()[-1]))

    return _read


@pytest.fixture(scope="session")
def seed_password(platform: Platform) -> Any:
    """Set a password for a user the journey created *at runtime* — the accepted invitee.

    Over HTTP (C8): the same forgot/reset round trip `tenants` uses for the owner and the
    administrator, because an accepted invitee has no credential either — `accept_invitation`
    deliberately creates none, exactly like `signup`. Takes an email rather than a user id: a
    reset is requested by address, and every caller already has the one it just invited.
    """

    def _set(email: str, password: str) -> None:
        with httpx.Client(base_url=platform.gateway, timeout=30.0) as http:
            _set_password_via_reset(http, email, password)

    return _set


@pytest.fixture(scope="session")
def seed_passwords(platform: Platform) -> Any:
    """`seed_password`, for several people at once — every `forgot` first, then every `reset`.

    A journey that onboards a cast of five (`test_approval_journey.py`'s Wave 0) used to call
    `seed_password` once per person, inside the loop that invites them: five independent
    forgot-wait-reset round trips, back to back. Each wait is bounded by how long the real
    chain behind it takes — auth's relay, notification's fold, notification's own relay, then
    its dispatcher — and under load the five waits simply add together, stretching a step that
    should be quick into the better part of a minute for no reason: nothing about one person's
    reset depends on another's.

    Requesting every `forgot` first lets the relay and the notification pipeline drain the
    whole batch in the passes they already run — the same reason `OutboxRelay._drain` claims up
    to `batch_size` rows per pass rather than one — so the five waits overlap instead of
    stacking, and this fixture waits once, not five times.
    """

    def _set(pairs: list[tuple[str, str]]) -> None:
        with httpx.Client(base_url=platform.gateway, timeout=30.0) as http:
            for email, _ in pairs:
                _request_reset(http, email)
            for email, password in pairs:
                _confirm_reset(http, email, password)

    return _set


@pytest.fixture(scope="session")
def mint_unsubscribe_token(platform: Platform) -> Any:
    """A real, validly-signed Engage unsubscribe token for one organization and address —
    what `test_engage_journey.py`'s edge-reachability step proves the gateway lets through
    with no bearer token at all."""

    def _mint(org_id: str, email: str) -> str:
        return _seed("_mint_unsubscribe_token.py", "notification", org_id, email)

    return _mint


# ------------------------------------------------- the Wave 0 exit journey's own machinery
#
# Everything below serves `test_approval_journey.py`. Each piece exists because the operation it
# performs genuinely has no HTTP surface — running a deploy-time reconcile, or advancing a
# background sweep's discovery clock. Nothing here substitutes for a request the journey could
# have made. Provisioning the tenant itself is no longer one of these (C8): `POST
# /api/v1/account/signup` is a real route now, so `approval_tenant` below uses it exactly as
# `tenants` does, rather than writing `account`'s schema directly.
#
# One fixture that used to live here is gone, and the reason is worth keeping. `link_employee` ran
# a raw `UPDATE` against `account.memberships.employee_id` over the admin DSN, because when the
# journey was written **nothing in `services/` could write that column** — the sole origin of the
# `sref` claim had no writer, which was a platform defect the suite reported rather than hid. It
# has one now (ADR-0008: `PATCH /api/v1/members/{membership_id}`, account's own endpoint on the
# `membership.write:ORG` guard), so step 7 links over HTTP with the owner's real token and the
# seeder was deleted. A seeder that outlives the gap it documented is how a suite ends up proving
# its own workaround.


@pytest.fixture(scope="session")
def approval_tenant(platform: Platform, tenants: dict[str, Any]) -> dict[str, Any]:
    """A third organization, created fresh for the approval journey, with a signed-in owner —
    over HTTP (C8), exactly like `tenants`.

    Its own tenant rather than alpha's, for two independent reasons. The CRM journey asserts
    that alpha gets **402** on `/api/v1/employees` because it never bought HRMS — activating
    HRMS on alpha would delete that assertion. And "a tenant is provisioned" is the first line
    of the Wave 0 exit criterion: proving it against an organization another test already
    activated products in would prove nothing.

    Depends on `tenants` for ordering, not for data: `_seed_product.py`, called from inside
    `tenants`, is what promotes `hrms` out of `soon`, and this journey's own activation step
    needs that to have already happened.
    """
    run = uuid.uuid4().hex[:8]
    slug = f"e2e-gamma-{run}"
    email = f"e2e-gamma-owner-{run}@example.com"
    with httpx.Client(base_url=platform.gateway, timeout=30.0) as http:
        org_id = _signup(
            http,
            org_name="Gamma Gearworks",
            slug=slug,
            full_name="Gamma Gearworks Owner",
            email=email,
        )
        _set_password_via_reset(http, email, PASSWORD)
        token = _login(http, email, PASSWORD, org_id)
        owner = _find_member(http, token, email)

    return {
        "org_id": org_id,
        "org_slug": slug,
        "owner": owner,
        "run": run,
        "password": PASSWORD,
    }


@pytest.fixture(scope="session")
def inventory_tenant(platform: Platform, tenants: dict[str, Any]) -> dict[str, Any]:
    """A fresh organization for the inventory journey, signed up over HTTP with nothing bought.

    Its own tenant, like `approval_tenant`: the journey's first assertion is a 402 on every
    inventory route before the owner activates the product, which alpha and beta could not be
    asked to share. Depends on `tenants` for ordering only — `_seed_product.py`, called from
    inside it, is what promotes `operations` out of `soon`.
    """
    run = uuid.uuid4().hex[:8]
    slug = f"e2e-inventory-{run}"
    email = f"e2e-inventory-owner-{run}@example.com"
    with httpx.Client(base_url=platform.gateway, timeout=20.0) as http:
        org_id = _signup(
            http,
            org_name="Indigo Looms",
            slug=slug,
            full_name="Indigo Looms Owner",
            email=email,
        )
        _set_password_via_reset(http, email, PASSWORD)
        token = _login(http, email, PASSWORD, org_id)
        owner = _find_member(http, token, email)

    return {
        "org_id": org_id,
        "org_slug": slug,
        "owner": owner,
        "email": email,
        "run": run,
        "password": PASSWORD,
    }


@pytest.fixture(scope="session")
def reconcile(platform: Platform) -> Any:
    """Run `tools/reconcile_permissions.py` — the real ADR-0007 §3 deploy step, not a stand-in.

    Deliberately not an API and deliberately not run at startup (ADR-0007 "What this rejects"),
    so a suite that wants a live tenant's roles widened has to invoke the same command an
    operator would. Run from the repository root with this suite's own pinned database and
    Redis, so it can never reach the shared development database.
    """

    def _run(product_key: str, org_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        args = [
            sys.executable,
            str(ROOT / "tools" / "reconcile_permissions.py"),
            product_key,
            "--org",
            org_id,
        ]
        if dry_run:
            args.append("--dry-run")
        result = subprocess.run(
            args,
            cwd=ROOT,
            env={
                **os.environ,
                "DATABASE_URL": DATABASE_URL,
                "REDIS_URL": REDIS_URL,
                "ENVIRONMENT": "ci",
                "PERMISSION_MANIFEST_DIR": str(ROOT / "libs" / "contracts" / "permissions"),
            },
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"reconcile_permissions.py {product_key} --org {org_id} failed:\n"
                f"{result.stdout}\n{result.stderr}"
            )
        # The last line is the fleet summary; the ones before it are per-organization diffs.
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        return {
            "summary": json.loads(lines[-1]),
            "organizations": [json.loads(line) for line in lines[:-1]],
        }

    return _run


@pytest.fixture(scope="session")
def db_clock(platform: Platform) -> Any:
    """PostgreSQL's `now()`, as a `datetime`. The only clock approval compares anything to."""

    def _now() -> Any:
        from datetime import datetime

        return datetime.fromisoformat(_seed("_db_clock.py", "approval").splitlines()[-1])

    return _now


@pytest.fixture(scope="session")
def sweep(platform: Platform) -> Any:
    """One pass of the real SLA escalation sweep, with discovery looking `minutes_ahead` ahead.

    See `_sweep.py` for why the sweep is driven through `tick(now=…)` rather than by waiting out
    a fifteen-minute SLA, and why the instant is computed by the database rather than here.
    """

    def _tick(minutes_ahead: int) -> dict[str, Any]:
        return dict(json.loads(_seed("_sweep.py", "approval", str(minutes_ahead)).splitlines()[-1]))

    return _tick


@pytest.fixture
def client(platform: Platform) -> Any:
    """Talks to the **gateway**, never to a service directly.

    That is the surface a real client has, and routing through it is what exercises the
    gateway's own work — audience, entitlement, permission resolution, header rewriting —
    rather than quietly bypassing it.
    """
    with httpx.Client(base_url=platform.gateway, timeout=15.0) as client:
        yield client
