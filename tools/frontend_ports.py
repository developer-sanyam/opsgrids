#!/usr/bin/env python3
"""Every frontend app gets its own dev port and its own preview port.

Seven apps once shared `vite preview --port 4340`, and three shared the dev port, so running two
of them side by side failed on `--strictPort` — or, worse, a stale server from one app answered
for another. A port is picked once, when an app is scaffolded, by someone looking at the other
apps; this makes the look mechanical.

Checks, per app with a `dev` script:

* `dev` and `preview` both pass `--port N` **and `--strictPort`**. Without the flag Vite quietly
  takes the next free port when N is busy, so two previews "run" and one of them is serving
  somewhere nobody looks — which is a collision that reports success;
* no two apps share a dev port, and no two share a preview port;
* **no app's dev or preview port is another app's** — a dev server on 4310 blocks a preview that
  wanted 4310 exactly as much as two previews would. An app's own dev and preview may share one
  number, because it is run as one or the other;
* `vite.config.ts` `server.port` and `preview.port`, where set, agree with the script that runs
  them — two sources for one number drift, and the script's `--port` wins silently.

    python tools/frontend_ports.py        # part of make frontend-lint
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

PORT_FLAG = re.compile(r"--port\s+(\d+)")
STRICT_FLAG = re.compile(r"--strictPort\b")
# `server: { … port: N,` and `preview: { … port: N,` — the block name says which script the
# number is for.
CONFIG_PORT = {
    "dev": re.compile(r"\bserver\s*:\s*\{[^{}]*?^\s*port:\s*(\d+)\s*,", re.MULTILINE | re.DOTALL),
    "preview": re.compile(
        r"\bpreview\s*:\s*\{[^{}]*?^\s*port:\s*(\d+)\s*,", re.MULTILINE | re.DOTALL
    ),
}


def problems(frontend: Path) -> list[str]:
    found: list[str] = []
    dev_ports: dict[int, str] = {}
    preview_ports: dict[int, str] = {}
    # Every (app, script, port) read, so the cross-kind check sees the second app to claim a
    # number too, which the first-come dictionaries above deliberately do not keep.
    claims: list[tuple[str, str, int]] = []
    for manifest in sorted(frontend.glob("*/package.json")):
        app = manifest.parent.name
        scripts: dict[str, str] = json.loads(manifest.read_text(encoding="utf-8")).get(
            "scripts", {}
        )
        if "dev" not in scripts:
            continue
        ports: dict[str, int] = {}
        for script, seen in (("dev", dev_ports), ("preview", preview_ports)):
            command = scripts.get(script, "")
            match = PORT_FLAG.search(command)
            if match is None:
                found.append(f"{app}: `{script}` sets no --port")
                continue
            if STRICT_FLAG.search(command) is None:
                found.append(
                    f"{app}: `{script}` sets no --strictPort, so a busy port moves it somewhere "
                    "else silently"
                )
            port = int(match.group(1))
            ports[script] = port
            claims.append((app, script, port))
            if port in seen:
                found.append(f"{app}: {script} port {port} is already {seen[port]}'s")
            else:
                seen[port] = app
        config = manifest.parent / "vite.config.ts"
        if config.exists():
            text = config.read_text(encoding="utf-8")
            for script, pattern in CONFIG_PORT.items():
                configured = pattern.search(text)
                if (
                    configured is not None
                    and script in ports
                    and int(configured.group(1)) != ports[script]
                ):
                    block = "preview" if script == "preview" else "server"
                    found.append(
                        f"{app}: vite.config.ts {block} serves on {configured.group(1)} "
                        f"but `{script}` passes --port {ports[script]}"
                    )

    # Across kinds: a preview may not sit on another app's dev port. (Checked once every app has
    # been read, so the order the apps are listed in does not matter.)
    for app, script, port in claims:
        if script != "preview":
            continue
        for other, other_script, other_port in claims:
            if other_script == "dev" and other_port == port and other != app:
                found.append(f"{app}: preview port {port} is {other}'s dev port")
    return found


def main() -> int:
    found = problems(FRONTEND)
    for problem in found:
        print(problem)
    if found:
        print(f"\n{len(found)} port problem(s). Pick a free port and use it in all three places.")
        return 1
    print("every app has its own dev and preview port")
    return 0


if __name__ == "__main__":
    sys.exit(main())
