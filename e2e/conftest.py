"""Start the platform for real, then let the tests drive it over HTTP.

Every other suite in this repository tests one service with the others stubbed. That is why
three outage-class defects reached the repository with every suite green: account-service served
none of the internal endpoints auth and the gateways call, the permission-version counter was
never incremented, and listing members raised because a join was mistaken for eager loading.
Each was a **contract between two components**, and a stub encodes what the author believed the
other side did.

So nothing here is stubbed. Three real processes, the real database, real tokens, real HTTP.

Services are subprocesses rather than in-process ASGI apps because every service exposes a
top-level `app` package and one process cannot import two of them. That constraint is why this
suite lives outside `services/` and drives everything over the network.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "e2e-Sufficiently-Long-Passw0rd!"

#: (name, port). Order matters only for readability; they start concurrently.
SERVICES = (
    ("account", 8111),
    ("product", 8112),
    ("auth", 8110),
    ("app-gateway", 8100),
)

GATEWAY = "http://localhost:8100"


def _wait_until_ready(
    port: int, name: str, proc: subprocess.Popen | None = None, timeout: float = 45.0
) -> None:
    """Poll readiness rather than sleeping a guessed interval.

    A fixed sleep is either slower than it needs to be or flaky on a loaded machine, and the
    failure it produces — a connection refused halfway through a journey — reads as a product
    bug rather than a startup race.
    """
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"http://localhost:{port}/health/ready", timeout=2.0)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return
            last = response.text[:200]
        except httpx.HTTPError as exc:
            last = str(exc)[:200]
        if proc is not None and proc.poll() is not None:
            # It exited. Its own output says why; a bare timeout would not, and the first
            # version of this harness hid a real configuration bug behind "connection refused".
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(
                f"{name} exited with code {proc.returncode} before becoming ready:\n"
                f"{output[-3000:]}"
            )
        time.sleep(0.3)

    output = ""
    if proc is not None and proc.stdout is not None and proc.poll() is not None:
        output = proc.stdout.read()[-3000:]
    raise RuntimeError(
        f"{name} was not ready on :{port} within {timeout}s — last: {last}\n{output}"
    )


@pytest.fixture(scope="session")
def platform() -> object:
    """The three services, running. Torn down whatever the tests do."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    processes: list[tuple[str, subprocess.Popen]] = []

    try:
        for name, port in SERVICES:
            proc = subprocess.Popen(
                [
                    "uv",
                    "run",
                    "uvicorn",
                    "app.main:app",
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                ],
                cwd=ROOT / "services" / name,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            processes.append((name, proc))

        for (name, port), (_, proc) in zip(SERVICES, processes, strict=True):
            _wait_until_ready(port, name, proc)

        yield object()
    finally:
        for _, proc in processes:
            proc.send_signal(signal.SIGINT)
        for name, proc in processes:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                print(f"{name} did not stop on SIGINT and was killed", file=sys.stderr)


def _seed(script: str, service: str, *args: str) -> str:
    """Run a seeder inside its own service directory."""
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(Path(__file__).parent / script),
            str(ROOT / "services" / service),
            *args,
        ],
        cwd=ROOT / "services" / service,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{script} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


@pytest.fixture(scope="session")
def account(platform: object) -> dict:
    """One organization, one member, one password — created through the real services' code."""
    seeded = json.loads(_seed("_seed_account.py", "account").splitlines()[-1])
    _seed("_seed_auth.py", "auth", seeded["user_id"], PASSWORD)
    return {**seeded, "password": PASSWORD}


@pytest.fixture
def client() -> httpx.Client:
    """Talks to the **gateway**, never to a service directly.

    That is the surface a real client has, and routing through it is what exercises the
    gateway's own work — audience, entitlement, rate limiting, header stripping — rather than
    quietly bypassing it.
    """
    with httpx.Client(base_url=GATEWAY, timeout=10.0) as client:
        yield client
