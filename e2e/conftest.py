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
#: `hrms.time.regularization.applied` and approval consumes that. Drop any one of these six
#: processes and the journey stalls at a different step, which is exactly what makes them worth
#: running for real.
#:
#: The bundle journey adds the commerce loop: subscription's relay publishes the
#: `commerce.subscription.*` event, product's consumer projects each member's activation, and
#: account's consumer (already here) installs each member's manifest.
BACKGROUND = (
    ("subscription-relay", "subscription", "app.relay"),
    ("product-consumer", "product", "app.consumers"),
    ("product-relay", "product", "app.relay"),
    ("account-consumer", "account", "app.consumers"),
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
    """The whole stack: six services, an outbox relay and an event consumer.

    The relay and the consumer are as much a part of the platform as the APIs — the journey's
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


@pytest.fixture(scope="session")
def tenants(platform: Platform) -> dict[str, Any]:
    """Two organizations, each with an owner, and CRM data in both.

    Two rather than one because "another tenant's data" is not assertable against a single
    tenant, and a cross-tenant test whose victim row does not exist passes for the wrong
    reason. Organizations are created through account-service's own `services.create_*`
    against the real database under row-level security — there is no HTTP route that creates
    one, by design: the tenant policy compares `id` to the bound organization and there is no
    id to bind before the row exists.
    """
    seeded = json.loads(_seed("_seed_account.py", "account").splitlines()[-1])

    # Credentials live in auth-service, in a different schema — which is the whole point of
    # the split, and the reason this suite exists. The first call clears the auth tables; the
    # rest must not, or each new password would wipe the one before it.
    first = True
    for org in ("alpha", "beta"):
        for role in ("owner", "admin"):
            person = seeded[org].get(role)
            if person is None:
                continue
            _seed(
                "_seed_auth.py",
                "auth",
                person["user_id"],
                PASSWORD,
                *([] if first else ["keep"]),
            )
            first = False

    # product-service's registry, and the CRM rows the entitled steps read back.
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
    """Read the outbox and the consumer's ledger directly, to say *which* hop broke."""

    def _read(service: str, org_id: str) -> dict[str, Any]:
        target = "product" if service == "product" else "account"
        raw = _seed("_probe.py", target, target, org_id)
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
def bundle_tenant(platform: Platform, tenants: dict[str, Any]) -> dict[str, Any]:
    """A fresh organization for the bundle journey, with a signed-in owner and nothing bought.

    Its own tenant, like `approval_tenant`, because the journey's first assertion is a 402 on
    every trio member — which alpha (CRM) and the approval tenant (HRMS) would each fail for a
    reason unrelated to bundles. Depends on `tenants` for ordering only.
    """
    run = f"bundle-{uuid.uuid4().hex[:8]}"
    seeded = json.loads(_seed("_seed_approval_org.py", "account", run).splitlines()[-1])
    _seed("_seed_auth.py", "auth", seeded["owner"]["user_id"], PASSWORD, "keep")
    return {**seeded, "run": run, "password": PASSWORD}


@pytest.fixture(scope="session")
def billing_tenant(platform: Platform, tenants: dict[str, Any]) -> dict[str, Any]:
    """A fresh organization for the billing-run journey: a signed-in owner, nothing bought.

    Its own tenant for the bundle tenant's reason — the journey's invoice and charge counts must
    be this organization's alone. Depends on `tenants` for ordering only.
    """
    run = f"billing-{uuid.uuid4().hex[:8]}"
    seeded = json.loads(_seed("_seed_approval_org.py", "account", run).splitlines()[-1])
    _seed("_seed_auth.py", "auth", seeded["owner"]["user_id"], PASSWORD, "keep")
    return {**seeded, "run": run, "password": PASSWORD}


@pytest.fixture(scope="session")
def billing_run(platform: Platform) -> Any:
    """One pass of subscription's real billing run, `seconds_ahead` past PostgreSQL's `now()`,
    calling the running billing and payment services with the run's shared signing key."""

    def _tick(seconds_ahead: int) -> dict[str, Any]:
        raw = _seed(
            "_billing_run.py",
            "subscription",
            str(seconds_ahead),
            extra_env={
                "BILLING_SERVICE_URL": platform.url("billing"),
                "PAYMENT_SERVICE_URL": platform.url("payment"),
                "INTERNAL_SERVICE_TOKEN": INTERNAL_TOKEN,
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

    The account seeder cannot know that user id: it exists only after an invitation is
    accepted over HTTP. `keep` leaves the existing rows alone, so the owner whose token
    created the invitation can still sign in.
    """

    def _set(user_id: str, password: str) -> None:
        _seed("_seed_auth.py", "auth", user_id, password, "keep")

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
# performs genuinely has no HTTP surface — provisioning a tenant, running a deploy-time reconcile,
# or advancing a background sweep's discovery clock. Nothing here substitutes for a request the
# journey could have made.
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
    """A third organization, created fresh for the approval journey, with a signed-in owner.

    Its own tenant rather than alpha's, for two independent reasons. The CRM journey asserts
    that alpha gets **402** on `/api/v1/employees` because it never bought HRMS — activating
    HRMS on alpha would delete that assertion. And "a tenant is provisioned" is the first line
    of the Wave 0 exit criterion: proving it against an organization another test already
    activated products in would prove nothing.

    Depends on `tenants` for ordering, not for data: `_seed_account.py` truncates the account
    schema on its way in, so this must run after it or lose the organization it just made.
    """
    run = uuid.uuid4().hex[:8]
    seeded = json.loads(_seed("_seed_approval_org.py", "account", run).splitlines()[-1])
    _seed("_seed_auth.py", "auth", seeded["owner"]["user_id"], PASSWORD, "keep")
    return {**seeded, "run": run, "password": PASSWORD}


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
