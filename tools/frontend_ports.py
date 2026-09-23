#!/usr/bin/env python3
"""Every frontend app gets its own dev port and its own preview port.

Seven apps once shared `vite preview --port 4340`, and three shared the dev port, so running two
of them side by side failed on `--strictPort` — or, worse, a stale server from one app answered
for another. A port is picked once, when an app is scaffolded, by someone looking at the other
apps; this makes the look mechanical.

Checks, per app with a `dev` script:

* `dev` and `preview` both pass `--port N`;
* no two apps share a dev port, and no two share a preview port;
* `vite.config.ts` `server.port`, where set, agrees with the `dev` script — two sources for one
  number drift, and the script's `--port` wins silently.

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
CONFIG_PORT = re.compile(r"^\s*port:\s*(\d+)\s*,", re.MULTILINE)


def problems(frontend: Path) -> list[str]:
    found: list[str] = []
    dev_ports: dict[int, str] = {}
    preview_ports: dict[int, str] = {}
    for manifest in sorted(frontend.glob("*/package.json")):
        app = manifest.parent.name
        scripts: dict[str, str] = json.loads(manifest.read_text(encoding="utf-8")).get(
            "scripts", {}
        )
        if "dev" not in scripts:
            continue
        ports: dict[str, int] = {}
        for script, seen in (("dev", dev_ports), ("preview", preview_ports)):
            match = PORT_FLAG.search(scripts.get(script, ""))
            if match is None:
                found.append(f"{app}: `{script}` sets no --port")
                continue
            port = int(match.group(1))
            ports[script] = port
            if port in seen:
                found.append(f"{app}: {script} port {port} is already {seen[port]}'s")
            else:
                seen[port] = app
        config = manifest.parent / "vite.config.ts"
        if "dev" in ports and config.exists():
            configured = CONFIG_PORT.search(config.read_text(encoding="utf-8"))
            if configured is not None and int(configured.group(1)) != ports["dev"]:
                found.append(
                    f"{app}: vite.config.ts serves on {configured.group(1)} but `dev` passes "
                    f"--port {ports['dev']}"
                )
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
