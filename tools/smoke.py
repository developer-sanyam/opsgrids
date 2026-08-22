#!/usr/bin/env python3
"""Boot a service for real and prove it answers.

The test suite builds the app in-process and overrides its dependencies, so a service can
pass every test and still fail to start: a lifespan that raises, an engine that cannot
connect as its role, a Redis URL nobody set. This is the one check that runs the actual
entrypoint — uvicorn, the lifespan, the pool, Redis — against the configured environment
and asks it the two questions an orchestrator would: are you up, and are you ready.

`/health/ready` answers 200 even when degraded, with the detail in the body, so the body
is what is asserted. Both documents are printed so a failure in CI is readable without
re-running anything.

    python tools/smoke.py [--service account] [--timeout 30]
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def wait_for(
    client: httpx.Client, url: str, server: subprocess.Popen[bytes], deadline: float
) -> dict[str, Any]:
    """Poll `url` until it answers 200, the server dies, or the deadline passes."""
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"server exited with status {server.returncode} before answering")
        try:
            response = client.get(url)
        except httpx.TransportError:
            response = None
        if response is not None and response.status_code == 200:
            body: dict[str, Any] = response.json()
            return body
        time.sleep(0.25)
    raise RuntimeError(f"{url} did not answer 200 within the timeout")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start a service and check its health")
    parser.add_argument("--service", default="account")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait")
    args = parser.parse_args()

    service_dir = ROOT / "services" / args.service
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    # The server's own output goes to a file rather than a pipe: a pipe nobody drains fills
    # and blocks the child, and the output is only interesting when something fails.
    with tempfile.TemporaryFile() as log:
        argv = [sys.executable, "-m", "uvicorn", "app.main:app"]
        argv += ["--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
        server = subprocess.Popen(  # noqa: S603 - this interpreter and fixed arguments
            argv, cwd=service_dir, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            deadline = time.monotonic() + args.timeout
            with httpx.Client(timeout=2.0) as client:
                health = wait_for(client, f"{base}/health", server, deadline)
                print(json.dumps(health, indent=2))
                ready = wait_for(client, f"{base}/health/ready", server, deadline)
                print(json.dumps(ready, indent=2))
        except RuntimeError as exc:
            print(f"smoke: {args.service}: {exc}", file=sys.stderr)
            log.seek(0)
            sys.stderr.write(log.read().decode(errors="replace"))
            return 1
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()

    if ready.get("status") != "ok":
        print(f"smoke: {args.service} is up but not ready: {ready}", file=sys.stderr)
        return 1
    print(f"smoke: {args.service} is up and ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
