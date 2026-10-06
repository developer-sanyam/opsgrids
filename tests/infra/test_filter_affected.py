"""Pins `tools/filter-affected.sh` — the extra narrowing `.github/workflows/ci.yml` layers
on top of `tools/shard.sh`'s partition, before `tools/fan-out.sh --stdin` ever sees the
list (see `tools/shard-filtered.sh`, which chains the three). Runs the real script as a
subprocess, never a reimplementation of its rules.

The one behaviour worth over-testing here: default-safe. Every target that uses this
filter (`make test-shard`, `make migration-shard`, `make migration-check-shard`) must keep
running every service, exactly as before this filter existed, whenever CI has not
deliberately set AFFECTED_ALL=false — which includes every local, by-hand invocation.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FILTER_AFFECTED = REPO_ROOT / "tools/filter-affected.sh"


def run(
    services: list[str],
    *,
    affected_all: str | None = None,
    affected_services: str | None = None,
) -> list[str]:
    env = os.environ.copy()
    env.pop("AFFECTED_ALL", None)
    env.pop("AFFECTED_SERVICES", None)
    if affected_all is not None:
        env["AFFECTED_ALL"] = affected_all
    if affected_services is not None:
        env["AFFECTED_SERVICES"] = affected_services
    stdin = "".join(f"{s}\n" for s in services)
    result = subprocess.run(
        ["bash", str(FILTER_AFFECTED)],
        input=stdin,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_with_neither_env_var_set_every_service_passes_through() -> None:
    # The state every local `make test-shard` runs in, and the state any future caller of
    # this filter gets if it forgets to set AFFECTED_ALL — both must mean "run everything".
    assert run(["auth", "billing", "finance"]) == ["auth", "billing", "finance"]


def test_affected_all_true_is_also_a_plain_passthrough() -> None:
    assert run(["auth", "billing"], affected_all="true", affected_services="auth") == [
        "auth",
        "billing",
    ]


def test_an_empty_affected_services_list_falls_back_to_passthrough() -> None:
    # A `changes` job bug that computes AFFECTED_ALL=false with nothing in the list must
    # not silently run zero services.
    assert run(["auth", "billing"], affected_all="false", affected_services="") == [
        "auth",
        "billing",
    ]


def test_narrows_to_exactly_the_named_services() -> None:
    out = run(
        ["auth", "billing", "finance"],
        affected_all="false",
        affected_services="auth finance",
    )
    assert out == ["auth", "finance"]


def test_a_shard_with_nothing_affected_comes_back_empty_not_an_error() -> None:
    result = subprocess.run(
        ["bash", str(FILTER_AFFECTED)],
        input="billing\nfinance\n",
        env={**os.environ, "AFFECTED_ALL": "false", "AFFECTED_SERVICES": "auth"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_service_names_match_exactly_not_as_substrings() -> None:
    # "auth" must not also let "auth2" or "app-gateway" through.
    out = run(
        ["auth", "auth2", "app-gateway"],
        affected_all="false",
        affected_services="auth",
    )
    assert out == ["auth"]


def test_newline_separated_affected_services_work_the_same_as_space_separated() -> None:
    out = run(
        ["auth", "billing", "finance"],
        affected_all="false",
        affected_services="auth\nfinance",
    )
    assert out == ["auth", "finance"]
