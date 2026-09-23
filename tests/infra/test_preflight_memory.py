"""`preflight.sh`'s memory-budget gate and `remote.sh record-budget`, end to end (memory-headroom
slice, review fix round 2: B1, B2/H3/H4, M5, M7, L8, L12).

The real scripts run in a scratch copy of `infrastructure/edge/`, against a fake `docker` on
`PATH` (a small Python program, below) that answers from `memory-budget.json` itself:
`docker ps` reports whatever the test says is running, `docker compose config --services`
reports whatever the current `COMPOSE_PROFILES` would configure, and `docker compose exec …
psql` records the SQL it was handed. Nothing here touches a real Docker daemon, a real
database, or the production VM — the point of stubbing at the `docker` boundary rather than
reimplementing the arithmetic is that these tests exercise the actual shell, quoting and
`set -e` behaviour a deploy will hit.

Every scenario reads the box's reference `MemTotal` (3862 MB, the figure the reviewer's
read-only check reported) from `EDGE_MEMINFO_FILE`, the one seam `preflight.sh` gained for this.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_SRC = REPO_ROOT / "infrastructure/edge"

sys.path.insert(0, str(EDGE_SRC / "lib"))
from memory_budget_totals import (  # noqa: E402
    expected_total_for_names,
    expected_total_mb,
    load_budget,
)

MEM_TOTAL_MB = 3862
MEM_TOTAL_KB = MEM_TOTAL_MB * 1024

STUB_DOCKER = f"""#!{sys.executable}
import json, os, sys

args = sys.argv[1:]
log = os.environ.get("STUB_SQL_LOG")

if args[:1] == ["info"]:
    sys.exit(0)
if args[:1] == ["ps"]:
    for name in filter(None, os.environ.get("STUB_RUNNING", "").split(",")):
        print(name)
    sys.exit(0)
if args[:1] == ["compose"]:
    if "config" in args and "--services" in args:
        budget = json.load(open(os.environ["STUB_BUDGET"]))
        # Real compose: an exported COMPOSE_PROFILES beats the one in `--env-file`.
        raw = os.environ.get("COMPOSE_PROFILES")
        if raw is None and "--env-file" in args:
            for line in open(args[args.index("--env-file") + 1]):
                if line.startswith("COMPOSE_PROFILES="):
                    raw = line.split("=", 1)[1].strip()
        profiles = {{p for p in (raw or "").split(",") if p}}
        for name, meta in budget["containers"].items():
            profile = meta.get("profile")
            if profile is None or profile in profiles:
                print(name)
        for name in filter(None, os.environ.get("STUB_EXTRA_SERVICES", "").split(",")):
            print(name)
        sys.exit(0)
    if "exec" in args and "psql" in args:
        sql = args[-1]
        if log:
            with open(log, "a") as fh:
                fh.write(sql + "\\n")
        if os.environ.get("STUB_PSQL_FAILS"):
            sys.exit(2)
        if "product.products" in sql:
            for key in filter(None, os.environ.get("STUB_AVAILABLE", "").split(",")):
                print(key)
        elif "product.activations" in sql:
            for key in filter(None, os.environ.get("STUB_ACTIVATED", "").split(",")):
                print(key)
        sys.exit(0)
    if "ps" in args:
        if os.environ.get("STUB_POSTGRES"):
            print("postgres")
        sys.exit(0)
sys.exit(0)
"""


@pytest.fixture
def budget() -> dict:
    return load_budget(EDGE_SRC / "memory-budget.json")


def _core(budget: dict) -> set[str]:
    return {n for n, m in budget["containers"].items() if m.get("profile") is None}


def _all_ten(budget: dict) -> set[str]:
    return set(budget["profiles"])


class Box:
    """A scratch edge directory plus the fake-docker environment to run its scripts in."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.edge = tmp_path / "edge"
        shutil.copytree(EDGE_SRC, self.edge, ignore=shutil.ignore_patterns(".env", "__pycache__"))
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text(STUB_DOCKER)
        docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
        if shutil.which("sha256sum") is None and shutil.which("shasum"):
            shim = self.bin / "sha256sum"
            shim.write_text('#!/bin/sh\nexec shasum -a 256 "$@"\n')
            shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        self.state = tmp_path / "state"
        self.state.mkdir()
        self.meminfo = tmp_path / "meminfo"
        self.meminfo.write_text(f"MemTotal:       {MEM_TOTAL_KB} kB\n")
        self.sql_log = tmp_path / "sql.log"
        self.running: set[str] = set()
        self.extra_services: set[str] = set()
        self.extra_env: dict[str, str] = {}
        self.write_env()

    def write_env(
        self,
        *,
        compose_profiles: str = "",
        edge_profiles: str | None = None,
        accepted_mb: str | None = None,
        omit: tuple[str, ...] = (),
    ) -> None:
        key = self.root / "signing.pem"
        key.write_text("not a real key")
        key.chmod(0o600)
        sites = re.findall(
            r"^https://([a-z0-9.-]*) \{$", (self.edge / "caddy/Caddyfile").read_text(), re.M
        )
        values = {
            "EDGE_HOST": "edge.opsgrids.test",
            "PUBLIC_ASSET_BASE_URL": "https://edge.opsgrids.test/opsgrids-public",
            "S3_PUBLIC_BUCKET": "opsgrids-public",
            "TLS_PLATFORM_HOSTS": ",".join(sites),
            "JWT_PRIVATE_KEY_HOST_PATH": str(key),
            "ENGAGE_PUBLIC_API_BASE": "https://api.opsgrids.test",
            "COMPOSE_PROFILES": compose_profiles,
        }
        if edge_profiles is not None:
            values["EDGE_PROFILES"] = edge_profiles
        if accepted_mb is not None:
            values["EDGE_BUDGET_ACCEPTED_MB"] = accepted_mb
        lines = []
        for line in (EDGE_SRC / ".env.example").read_text().splitlines():
            m = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
            if not m or m.group(1) in omit:
                continue
            name = m.group(1)
            if name in values:
                lines.append(f"{name}={values[name]}")
            elif name in ("EDGE_PROFILES", "EDGE_BUDGET_ACCEPTED_MB"):
                continue
            else:
                lines.append(f"{name}=dummy-value-{name.lower()}")
        env_file = self.edge / ".env"
        env_file.write_text("\n".join(lines) + "\n")
        env_file.chmod(0o600)

    def env(self) -> dict[str, str]:
        base = {k: v for k, v in os.environ.items() if not k.startswith(("EDGE_", "COMPOSE_"))}
        base.update(
            PATH=f"{self.bin}{os.pathsep}{base.get('PATH', '')}",
            EDGE_MEMINFO_FILE=str(self.meminfo),
            EDGE_STATE_DIR=str(self.state),
            STUB_RUNNING=",".join(sorted(self.running)),
            STUB_EXTRA_SERVICES=",".join(sorted(self.extra_services)),
            STUB_BUDGET=str(self.edge / "memory-budget.json"),
            STUB_SQL_LOG=str(self.sql_log),
        )
        base.update(self.extra_env)
        return base

    def run(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.edge / script), *args],
            capture_output=True,
            text=True,
            env=self.env(),
            cwd=self.edge,
        )

    def preflight(self) -> subprocess.CompletedProcess[str]:
        return self.run("preflight.sh")


def _priced_mb(result: subprocess.CompletedProcess[str]) -> int:
    m = re.search(
        r"memory budget \(post-deploy[^)]*\): (\d+)M of (\d+)M", result.stdout + result.stderr
    )
    assert m, f"no memory budget line in:\n{result.stdout}\n{result.stderr}"
    assert int(m.group(2)) == MEM_TOTAL_MB
    return int(m.group(1))


def _memory_failed(result: subprocess.CompletedProcess[str]) -> bool:
    return "FAIL  memory budget" in result.stderr


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


# ------------------------------------------------------------------ B1: price the POST-deploy set


def test_a_deploy_adding_a_profile_is_priced_above_the_running_set(box: Box, budget: dict) -> None:
    """The box runs core only; this deploy turns `inventory` on. Pricing only what is running
    (the old behaviour) would call this deploy free."""
    box.running = _core(budget)
    box.write_env(compose_profiles="inventory")
    result = box.preflight()
    priced = _priced_mb(result)
    assert priced == expected_total_mb(budget, {"inventory"})
    assert priced > expected_total_for_names(budget, box.running)


def test_an_orphan_from_a_disabled_profile_is_still_priced(box: Box, budget: dict) -> None:
    """Compose does not stop a container whose profile was removed (H2) — it keeps costing
    memory, and the new compose file still defines its service."""
    box.running = _core(budget) | {"operations"}
    box.write_env(compose_profiles="")  # inventory no longer declared
    priced = _priced_mb(box.preflight())
    assert priced == expected_total_for_names(budget, _core(budget) | {"operations"})


def test_a_running_container_whose_service_was_deleted_is_not_priced(
    box: Box, budget: dict
) -> None:
    box.running = _core(budget) | {"service-the-new-compose-deleted"}
    box.write_env(compose_profiles="")
    assert _priced_mb(box.preflight()) == expected_total_for_names(budget, _core(budget))


def test_a_fresh_box_with_nothing_running_is_priced_from_its_declared_profiles(
    box: Box, budget: dict
) -> None:
    box.write_env(compose_profiles="inventory,storefront")
    assert _priced_mb(box.preflight()) == expected_total_mb(budget, {"inventory", "storefront"})


def test_record_budget_prices_the_box_the_same_way_the_gate_does(box: Box, budget: dict) -> None:
    box.running = _core(budget)
    box.write_env(compose_profiles="inventory")
    gate = _priced_mb(box.preflight())
    result = box.run("remote.sh", "record-budget")
    assert result.returncode == 0, result.stderr
    assert (box.state / "edge-budget.last-ok").read_text().strip() == str(gate)


# ------------------------------------------------- B2/H3/H4: the explicit owner ceiling


def _ten_profile_total(budget: dict) -> int:
    return expected_total_mb(budget, _all_ten(budget))


def _all_ten_csv(budget: dict) -> str:
    return ",".join(budget["profiles"])


def test_over_the_hard_line_with_no_ceiling_refuses(box: Box, budget: dict) -> None:
    box.write_env(compose_profiles=_all_ten_csv(budget))
    result = box.preflight()
    assert _priced_mb(result) > MEM_TOTAL_MB * budget["host"]["hard_budget_percent"] // 100
    assert _memory_failed(result)
    assert "EDGE_BUDGET_ACCEPTED_MB" in result.stderr
    assert result.returncode != 0


def test_over_the_hard_line_but_at_or_under_the_accepted_ceiling_warns_and_names_the_overcommit(
    box: Box, budget: dict
) -> None:
    total = _ten_profile_total(budget)
    box.write_env(compose_profiles=_all_ten_csv(budget), accepted_mb=str(total))
    result = box.preflight()
    assert not _memory_failed(result), result.stderr
    assert "OVER THE HARD BUDGET" in result.stderr
    assert f"EDGE_BUDGET_ACCEPTED_MB={total}" in result.stderr
    assert re.search(r"\(\d+%\)", result.stderr)  # the overcommit percentage is named


def test_over_the_accepted_ceiling_still_refuses(box: Box, budget: dict) -> None:
    total = _ten_profile_total(budget)
    box.write_env(compose_profiles=_all_ten_csv(budget), accepted_mb=str(total - 1))
    result = box.preflight()
    assert _memory_failed(result)
    assert "owner-accepted ceiling" in result.stderr


def test_a_non_numeric_ceiling_is_a_failure_not_a_silent_ignore(box: Box, budget: dict) -> None:
    box.write_env(compose_profiles=_all_ten_csv(budget), accepted_mb="4200MB")
    result = box.preflight()
    assert "must be a whole number of megabytes" in result.stderr
    assert result.returncode != 0


def test_last_ok_is_informational_only_and_never_grandfathers_a_deploy(
    box: Box, budget: dict
) -> None:
    """The removed ratchet: a recorded last-ok equal to this deploy's own total used to make an
    over-the-line deploy pass. It must not — the ceiling is `.env`'s, not the last success's."""
    total = _ten_profile_total(budget)
    (box.state / "edge-budget.last-ok").write_text(f"{total}\n")
    box.write_env(compose_profiles=_all_ten_csv(budget))
    result = box.preflight()
    assert _memory_failed(result)
    assert f"last successful deploy priced at: {total}M (informational only" in result.stdout


def test_recovery_after_a_failed_health_check_needs_nothing_special(box: Box, budget: dict) -> None:
    """A stale or missing last-ok changes nothing: the verdict is the same with or without one."""
    box.write_env(
        compose_profiles=_all_ten_csv(budget), accepted_mb=str(_ten_profile_total(budget))
    )
    without = box.preflight()
    (box.state / "edge-budget.last-ok").write_text("1\n")
    with_stale = box.preflight()
    assert not _memory_failed(without)
    assert not _memory_failed(with_stale)


def test_under_the_soft_line_is_ok_whatever_the_ceiling(box: Box, budget: dict) -> None:
    box.write_env(compose_profiles="inventory", accepted_mb="1")
    result = box.preflight()
    assert not _memory_failed(result)
    assert "OVER THE HARD BUDGET" not in result.stderr


# ------------------------------------------------------------------------- M5: optional keys


def test_edge_profiles_and_the_accepted_ceiling_may_be_absent_from_an_existing_env(
    box: Box,
) -> None:
    box.write_env(omit=("EDGE_PROFILES", "EDGE_BUDGET_ACCEPTED_MB"))
    result = box.preflight()
    assert "missing or empty in .env" not in result.stderr
    assert "every key in .env.example is set" in result.stdout


def test_any_other_absent_key_is_still_reported_missing(box: Box) -> None:
    box.write_env(omit=("RABBITMQ_PASSWORD",))
    assert "missing or empty in .env: RABBITMQ_PASSWORD" in box.preflight().stderr


# ------------------------------------------------------ M7: unknown containers priced, not free


def test_a_service_with_no_budget_entry_is_priced_as_background_and_named(
    box: Box, budget: dict
) -> None:
    box.extra_services = {"hotfix-sidecar"}
    box.write_env(compose_profiles="")
    result = box.preflight()
    assert _priced_mb(result) == (
        expected_total_mb(budget, set()) + budget["kinds"]["background"]["expected_rss_mb"]
    )
    assert "hotfix-sidecar" in result.stderr
    assert "no memory-budget.json entry" in result.stderr


# ------------------------------------------------------------ L12: which activations count


def test_the_activations_query_counts_only_live_activations_and_skips_e2e_orgs(box: Box) -> None:
    box.extra_env = {"STUB_POSTGRES": "1", "STUB_ACTIVATED": "operations"}
    box.write_env(compose_profiles="inventory")
    box.preflight()
    sql = box.sql_log.read_text()
    activations_sql = next(line for line in sql.splitlines() if "product.activations" in line)
    assert "status in ('trial','active')" in activations_sql
    assert "e2e-%" in activations_sql


def test_a_failing_activations_query_is_a_warning_never_read_as_zero(box: Box) -> None:
    box.extra_env = {"STUB_POSTGRES": "1", "STUB_PSQL_FAILS": "1", "STUB_ACTIVATED": "operations"}
    box.write_env(compose_profiles="")  # inventory NOT enabled, and operations IS activated
    result = box.preflight()
    assert "could not read product.activations" in result.stderr
    assert "customers would lose access" not in result.stderr


def test_a_real_activation_with_no_service_enabled_is_refused(box: Box) -> None:
    box.extra_env = {"STUB_POSTGRES": "1", "STUB_ACTIVATED": "operations", "STUB_AVAILABLE": ""}
    box.write_env(compose_profiles="")
    result = box.preflight()
    assert "customers would lose access" in result.stderr
    assert result.returncode != 0


# --------------------------------------------------------------- L8: record_budget is loud


def test_record_budget_says_so_when_it_cannot_price_the_box(box: Box) -> None:
    (box.edge / "memory-budget.json").unlink()
    result = box.run("remote.sh", "record-budget")
    assert "record-budget: no memory-budget.json" in result.stderr
    assert not (box.state / "edge-budget.last-ok").exists()


def test_record_budget_says_so_when_the_state_dir_is_unwritable(box: Box, budget: dict) -> None:
    if os.geteuid() == 0:  # root ignores directory permissions
        pytest.skip("cannot make a directory unwritable to root")
    box.state.chmod(0o500)
    try:
        result = box.run("remote.sh", "record-budget")
    finally:
        box.state.chmod(0o700)
    assert "record-budget:" in result.stderr and "not recorded" in result.stderr


def test_record_budget_writes_atomically_and_leaves_no_temp_file(box: Box, budget: dict) -> None:
    box.write_env(compose_profiles="inventory")
    result = box.run("remote.sh", "record-budget")
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in box.state.iterdir()) == ["edge-budget.last-ok"]
