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
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------- isolation

#: The worktree database. Pinned here and passed explicitly to every subprocess: the live
#: demo lives in `opsgrids`, and no `.env` on any developer's machine may steer this suite
#: into it.
DATABASE_URL = "postgresql+asyncpg://opsgrids:opsgrids@localhost:5434/opsgrids_wt"

#: Its own Redis db. The gateway keeps permission versions and rate-limit counters here, and
#: sharing db 0 with a running stack makes both flaky.
REDIS_URL = "redis://localhost:6380/9"

#: Its own broker vhost, created on demand. Consumer queue names are compiled into the
#: services (`account.events`), so on the default vhost this suite and a running stack would
#: round-robin the same queue — and an event minted here could be consumed by a process
#: writing to the live database.
VHOST = "opsgrids_e2e"
RABBITMQ_URL = f"amqp://opsgrids:opsgrids@localhost:5673/{VHOST}"
RABBITMQ_MGMT = "http://localhost:15673"

PASSWORD = "e2e-Sufficiently-Long-Passw0rd!"

#: Every service this journey needs running. The gateway is last only for readability.
SERVICES = ("account", "product", "auth", "customer", "deal", "app-gateway")

#: The committed route table addresses services by port. Rewriting by port rather than by
#: prefix keeps this harness in step with `routes.json` when a prefix is added or moved —
#: and `_generate_routes` fails loudly if a service it boots ends up with no route.
UPSTREAM_PORTS = {8110: "auth", 8111: "account", 8112: "product", 8130: "customer", 8131: "deal"}

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

    Routes whose upstream this suite does not boot are left pointing where they were. That
    is intentional for the unentitled-product step: the gateway refuses with 402 *before* it
    proxies, so the assertion holds without an employee-service running — and if the gate
    ever regressed to proxying first, the request would fail loudly instead of passing.
    """
    table = json.loads((ROOT / "services" / "app-gateway" / "routes.json").read_text())
    routed: set[str] = set()
    for route in table["routes"]:
        port = int(route["upstream"].rsplit(":", 1)[1])
        service = UPSTREAM_PORTS.get(port)
        if service is not None:
            route["upstream"] = f"http://127.0.0.1:{ports[service]}"
            routed.add(service)

    missing = {s for s in SERVICES if s != "app-gateway"} - routed
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


def _seed(script: str, service: str, *args: str) -> str:
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
        env={**os.environ, "DATABASE_URL": DATABASE_URL, "REDIS_URL": REDIS_URL},
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

        # The two background processes the journey's event step depends on. product-service
        # stages `core.product.activated`; its relay publishes it; account-service's consumer
        # installs the manifest. Defect (a) killed the middle hop silently.
        for name, service, module in (
            ("product-relay", "product", "app.relay"),
            ("account-consumer", "account", "app.consumers"),
        ):
            proc = _spawn(
                name, [sys.executable, "-m", module], ROOT / "services" / service, env, log_dir
            )
            stack.processes.append((name, proc))

        # Neither serves HTTP, so readiness is "still alive a moment later" — enough to catch
        # the RLS-role refusal that `ENVIRONMENT=ci` now turns into an exit.
        time.sleep(3.0)
        for name in ("product-relay", "account-consumer"):
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
def seed_password(platform: Platform) -> Any:
    """Set a password for a user the journey created *at runtime* — the accepted invitee.

    The account seeder cannot know that user id: it exists only after an invitation is
    accepted over HTTP. `keep` leaves the existing rows alone, so the owner whose token
    created the invitation can still sign in.
    """

    def _set(user_id: str, password: str) -> None:
        _seed("_seed_auth.py", "auth", user_id, password, "keep")

    return _set


@pytest.fixture
def client(platform: Platform) -> Any:
    """Talks to the **gateway**, never to a service directly.

    That is the surface a real client has, and routing through it is what exercises the
    gateway's own work — audience, entitlement, permission resolution, header rewriting —
    rather than quietly bypassing it.
    """
    with httpx.Client(base_url=platform.gateway, timeout=15.0) as client:
        yield client
