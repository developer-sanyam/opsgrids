"""The one subprocess-per-service helper the contract exporters share.

Every service exposes a top-level `app`, so one interpreter can hold exactly one of them —
the same reason tests and mypy loop per service. An exporter therefore re-runs *itself* with
`--one <target>` and the service directory as cwd, and that child does the actual import.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_one(script: str | Path, service: Path, target: Path) -> None:
    """Run `script --one <target>` in a fresh interpreter with `service` as the working
    directory; raises `CalledProcessError` if the child fails."""
    subprocess.run(  # noqa: S603 - this interpreter, a tools script, a path we chose
        [sys.executable, str(script), "--one", str(target)],
        cwd=service,
        check=True,
    )
