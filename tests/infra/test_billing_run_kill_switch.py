"""The billing run is off on the edge unless `.env` turns it on (ADR-0005 addendum, review fix 6).

Its first passes on a box with history convert every expired trial and bill every overdue period,
so the default everywhere a deploy reads it is `false`: the compose interpolation default, the
committed example (which documents it commented out, so preflight does not require it), and
preflight's warning when it is anything but `true`. Checked here, in CI's static job, because a
default flipped back to `true` would ship silently.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

EDGE_DIR = Path(__file__).resolve().parents[2] / "infrastructure/edge"


def test_compose_defaults_the_billing_run_off() -> None:
    compose = yaml.safe_load((EDGE_DIR / "compose.prod.yml").read_text())
    env = compose["services"]["subscription-worker"]["environment"]
    assert env["BILLING_RUN_ENABLED"] == "${BILLING_RUN_ENABLED:-false}"


def test_the_example_documents_it_without_requiring_it() -> None:
    example = (EDGE_DIR / ".env.example").read_text()
    assert "# BILLING_RUN_ENABLED=true" in example
    # preflight requires every uncommented key; this one must stay optional.
    assert not re.search(r"^BILLING_RUN_ENABLED=", example, flags=re.MULTILINE)


def test_preflight_warns_unless_it_is_on() -> None:
    preflight = (EDGE_DIR / "preflight.sh").read_text()
    assert '"${BILLING_RUN_ENABLED:-}" != "true"' in preflight
    assert "the billing run is OFF" in preflight


# --- the cutover (final review, fix 3) ------------------------------------------------------


def test_compose_passes_the_cutover_to_the_worker() -> None:
    compose = yaml.safe_load((EDGE_DIR / "compose.prod.yml").read_text())
    env = compose["services"]["subscription-worker"]["environment"]
    assert env["BILLING_RUN_NOT_BEFORE"] == "${BILLING_RUN_NOT_BEFORE:-}"


def test_the_example_documents_the_cutover_without_requiring_it() -> None:
    example = (EDGE_DIR / ".env.example").read_text()
    assert re.search(
        r"^# BILLING_RUN_NOT_BEFORE=\d{4}-\d{2}-\d{2}T.*[+-]\d{2}:\d{2}$", example, re.M
    )
    assert not re.search(r"^BILLING_RUN_NOT_BEFORE=", example, flags=re.MULTILINE)


def _billing_run_block() -> str:
    preflight = (EDGE_DIR / "preflight.sh").read_text()
    block = preflight[preflight.index('if [ "${BILLING_RUN_ENABLED:-}" != "true" ]') :]
    return block[: block.index("\nfi\n") + 4]


def _gnu_date_path(tmp_path: Path) -> str:
    """A PATH on which `date` is GNU date — the VM's (Ubuntu) — or skip. macOS ships BSD date;
    Homebrew's coreutils install it as `gdate`."""
    if (
        subprocess.run(
            ["date", "-u", "-d", "2026-01-01T00:00:00Z", "+%s"], capture_output=True
        ).returncode
        == 0
    ):
        return os.environ["PATH"]
    gdate = shutil.which("gdate")
    if gdate is None:
        pytest.skip("GNU date is not available (the VM has it; install coreutils locally)")
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "date").symlink_to(gdate)
    return f"{shim}:{os.environ['PATH']}"


def _preflight(tmp_path: Path, enabled: str, cutover: str | None) -> tuple[str, str]:
    """Run preflight's billing-run block as preflight does: `set -euo pipefail`, its `ok`/`bad`."""
    script = (
        "set -euo pipefail\n"
        "ok() { printf '  ok    %s\\n' \"$*\"; }\n"
        "bad() { printf '  FAIL  %s\\n' \"$*\" >&2; }\n" + _billing_run_block()
    )
    env = {"PATH": _gnu_date_path(tmp_path), "BILLING_RUN_ENABLED": enabled}
    if cutover is not None:
        env["BILLING_RUN_NOT_BEFORE"] = cutover
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return done.stdout, done.stderr


@pytest.mark.parametrize(
    ("cutover", "why"),
    [
        (None, "is empty"),
        ("", "is empty"),
        ("0", "must be YYYY-MM-DDTHH:MM:SS±HH:MM"),
        ("2026", "must be YYYY-MM-DDTHH:MM:SS±HH:MM"),
        ("2026-10-01T09:00:00", "must be YYYY-MM-DDTHH:MM:SS±HH:MM"),
        ("2026-10-01 09:00:00+05:30", "must be YYYY-MM-DDTHH:MM:SS±HH:MM"),
        ("2026-09-31T00:00:00Z", "is not a real instant"),
    ],
)
def test_preflight_fails_an_enabled_run_without_a_real_cutover(
    tmp_path: Path, cutover: str | None, why: str
) -> None:
    _, err = _preflight(tmp_path, "true", cutover)
    assert "FAIL" in err and why in err


@pytest.mark.parametrize(
    "cutover", ["2026-01-01T09:00:00+05:30", "2026-01-01T03:30:00Z", "2026-01-01T03:30:00.25Z"]
)
def test_preflight_accepts_a_real_cutover(tmp_path: Path, cutover: str) -> None:
    out, err = _preflight(tmp_path, "true", cutover)
    assert "FAIL" not in err and "in the future" not in err
    assert f"cutover {cutover}" in out


def test_preflight_warns_about_a_cutover_in_the_future(tmp_path: Path) -> None:
    out, err = _preflight(tmp_path, "true", "2099-10-01T09:00:00+05:30")
    assert "FAIL" not in err
    assert "in the future" in err and "nothing is invoiced until then" in err
    assert "billing run enabled" in out


def test_preflight_ignores_the_cutover_while_the_run_is_off(tmp_path: Path) -> None:
    _, err = _preflight(tmp_path, "false", "garbage")
    assert "the billing run is OFF" in err and "FAIL" not in err


def test_the_preflight_pattern_is_the_one_the_worker_accepts() -> None:
    """One shape, stated twice (bash and Python): they must not drift."""
    pattern = re.search(r"grep -Eq '([^']+)'", _billing_run_block())
    assert pattern is not None
    config = (EDGE_DIR.parents[1] / "services/subscription/app/config.py").read_text()
    worker = re.search(r'CUTOVER_PATTERN = re\.compile\(\s*r"([^"]+)"', config)
    assert worker is not None
    assert pattern.group(1) == worker.group(1).replace(r"\d", "[0-9]")
