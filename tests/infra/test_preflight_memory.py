"""`preflight.sh`'s memory-budget gate and `remote.sh record-budget`, end to end (memory-headroom
slice, review fix rounds 2 and 3: B1, B2/H3/H4, M5, M7, L8, L12; round 3: fail-closed pricing,
restarting/paused containers, the ceiling's upper bound, `COMPOSE_PROFILES=*`, suspended
activations, the always-exported profile list).

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
    if os.environ.get("STUB_PS_FAILS"):
        print("Cannot connect to the Docker daemon", file=sys.stderr)
        sys.exit(1)
    # Honour `--filter status=X` like the real thing: several of the same key are ORed, and a
    # caller that asks for `running` only never hears about a restarting or paused container.
    wanted = {{args[i + 1].split("=", 1)[1] for i, a in enumerate(args[:-1])
              if a == "--filter" and args[i + 1].startswith("status=")}}
    by_status = {{
        "running": os.environ.get("STUB_RUNNING", ""),
        "restarting": os.environ.get("STUB_RESTARTING", ""),
        "paused": os.environ.get("STUB_PAUSED", ""),
        "exited": os.environ.get("STUB_EXITED", ""),
    }}
    for status, names in by_status.items():
        if wanted and status not in wanted:
            continue
        for name in filter(None, names.split(",")):
            print(name)
    sys.exit(0)
if args[:1] == ["compose"]:
    if "run" in args and "--entrypoint" in args and "id" in args:
        print("999")  # the image's uid/gid, as `remote.sh up`'s ops-monitor state-dir step asks
        sys.exit(0)
    if "up" in args:
        # Only the general `up`: the gateways-first one before it is test_remote_up_order's.
        if "--remove-orphans" in args:
            with open(os.environ["STUB_UP_LOG"], "a") as fh:
                fh.write(os.environ.get("COMPOSE_PARALLEL_LIMIT", "") + "\\n")
            with open(os.environ["STUB_UP_LOG"] + ".profiles", "a") as fh:
                fh.write(os.environ.get("COMPOSE_PROFILES", "<unset>") + "\\n")
        sys.exit(0)
    if "config" in args and "--profiles" in args:
        budget = json.load(open(os.environ["STUB_BUDGET"]))
        for name in budget["profiles"]:
            print(name)
        sys.exit(0)
    if "config" in args and "--services" in args:
        if os.environ.get("STUB_COMPOSE_FAILS"):
            print("error while interpolating services.x.environment: required variable is missing",
                  file=sys.stderr)
            sys.exit(1)
        if os.environ.get("STUB_COMPOSE_EMPTY"):
            sys.exit(0)
        budget = json.load(open(os.environ["STUB_BUDGET"]))
        # Real compose: an exported COMPOSE_PROFILES beats the one in `--env-file`.
        raw = os.environ.get("COMPOSE_PROFILES")
        if raw is None and "--env-file" in args:
            for line in open(args[args.index("--env-file") + 1]):
                if line.startswith("COMPOSE_PROFILES="):
                    raw = line.split("=", 1)[1].strip()
        profiles = {{p for p in (raw or "").split(",") if p}}
        every = "*" in profiles  # real compose: `*` enables every profile
        for name, meta in budget["containers"].items():
            profile = meta.get("profile")
            if profile is None or every or profile in profiles:
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
        self.restarting: set[str] = set()
        self.paused: set[str] = set()
        self.exited: set[str] = set()
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
            r"^https://([a-z0-9.-]+) \{$", (self.edge / "caddy/Caddyfile").read_text(), re.M
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
            STUB_RESTARTING=",".join(sorted(self.restarting)),
            STUB_PAUSED=",".join(sorted(self.paused)),
            STUB_EXITED=",".join(sorted(self.exited)),
            STUB_EXTRA_SERVICES=",".join(sorted(self.extra_services)),
            STUB_BUDGET=str(self.edge / "memory-budget.json"),
            STUB_SQL_LOG=str(self.sql_log),
            STUB_UP_LOG=str(self.root / "up.log"),
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


def _priced_mb(result: subprocess.CompletedProcess[str], mem_total_mb: int = MEM_TOTAL_MB) -> int:
    """The post-deploy total preflight printed: "1964M of 3862M MemTotal (50.8%)" under the
    box's memory, "4581M against 3862M MemTotal: 719M over MemTotal (118.6%)" over it."""
    m = re.search(
        r"memory budget \(post-deploy[^)]*\): (\d+)M (?:of|against) (\d+)M",
        result.stdout + result.stderr,
    )
    assert m, f"no memory budget line in:\n{result.stdout}\n{result.stderr}"
    assert int(m.group(2)) == mem_total_mb
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


def _small_box_over_hard_line(budget: dict, box: Box) -> tuple[int, int]:
    """Points `box.meminfo` at a box small enough that the real ten-profile total is over its own
    hard line, and returns `(total, mem_total_mb)`.

    The worker-folding follow-up to the memory-headroom slice (2026-09-24) brought the real
    ten-profile total comfortably under the real box's hard line (3691M against 3862M, 95.6% —
    see memory-budget.json's own header). `apply_accepted_ceiling` only ever touches an
    already-`refuse` verdict — `ok` and `warn` pass through unchanged — so every test below that
    exercises the ceiling machinery (a ceiling above the cap, the last-ok ratchet, slack
    reporting, ...) needs a base scenario that is genuinely over the hard line to have anything
    to exercise at all. A smaller simulated `MemTotal` gets there without inventing a profile
    combination that does not exist."""
    total = _ten_profile_total(budget)
    hard_percent = budget["host"]["hard_budget_percent"]
    # The largest MemTotal (MB) for which `total` is still at or over `hard_percent`, minus a
    # margin so integer division on either side of the calculation cannot land exactly on the
    # line by coincidence.
    mem_total_mb = total * 100 // hard_percent - 10
    box.meminfo.write_text(f"MemTotal:       {mem_total_mb * 1024} kB\n")
    return total, mem_total_mb


def test_over_the_hard_line_with_no_ceiling_refuses(box: Box, budget: dict) -> None:
    _total, mem_total_mb = _small_box_over_hard_line(budget, box)
    box.write_env(compose_profiles=_all_ten_csv(budget))
    result = box.preflight()
    assert (
        _priced_mb(result, mem_total_mb)
        > mem_total_mb * budget["host"]["hard_budget_percent"] // 100
    )
    assert _memory_failed(result)
    assert "EDGE_BUDGET_ACCEPTED_MB" in result.stderr
    assert result.returncode != 0


def test_over_the_hard_line_but_at_or_under_the_accepted_ceiling_warns_and_names_the_overcommit(
    box: Box, budget: dict
) -> None:
    total, mem_total_mb = _small_box_over_hard_line(budget, box)
    box.write_env(compose_profiles=_all_ten_csv(budget), accepted_mb=str(total))
    result = box.preflight()
    assert not _memory_failed(result), result.stderr
    assert "OVER THE HARD BUDGET" in result.stderr
    assert f"EDGE_BUDGET_ACCEPTED_MB={total}" in result.stderr
    # The overcommit is named in megabytes and to one decimal — a floored "100%" reads as fine
    # for a box that is 3894 MB on 3862 MB (round 3, M5).
    over = total - mem_total_mb
    assert over > 0
    assert f"{over}M over MemTotal" in result.stderr
    assert re.search(rf"\({total * 1000 // mem_total_mb // 10}\.\d%\)", result.stderr)


def test_over_the_accepted_ceiling_still_refuses(box: Box, budget: dict) -> None:
    total, _mem_total_mb = _small_box_over_hard_line(budget, box)
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
    total, _mem_total_mb = _small_box_over_hard_line(budget, box)
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


def test_the_activations_query_counts_suspended_and_skips_e2e_orgs(box: Box) -> None:
    box.extra_env = {"STUB_POSTGRES": "1", "STUB_ACTIVATED": "operations"}
    box.write_env(compose_profiles="inventory")
    box.preflight()
    sql = box.sql_log.read_text()
    activations_sql = next(line for line in sql.splitlines() if "product.activations" in line)
    # `suspended` counts: `product.project_subscription` flips it back to `active` when a
    # payment lands, onto a service this box would not be running (round 3).
    assert "status in ('trial','active','suspended')" in activations_sql
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


# ------------------------------------------ round 3, HIGH: the gate fails closed, not open


def _memory_row_failed_closed(result: subprocess.CompletedProcess[str]) -> None:
    """The one invariant every fail-closed case shares: preflight FAILED, on a memory row that
    says it could not price the box — never a green "ok memory budget … 0M (0%)"."""
    assert result.returncode != 0, result.stdout
    assert "FAIL  memory budget could not be priced" in result.stderr
    assert "ok    memory budget" not in result.stdout


def test_a_compose_config_failure_fails_the_memory_row_instead_of_pricing_zero(
    box: Box,
) -> None:
    """`docker compose config --services` exiting non-zero used to send its stderr to /dev/null
    and its status to nowhere: an empty post-deploy set, and a green "0M (0%)" — a gate that
    passes exactly when it cannot see."""
    box.extra_env = {"STUB_COMPOSE_FAILS": "1"}
    box.write_env(compose_profiles="inventory")
    result = box.preflight()
    _memory_row_failed_closed(result)
    assert "docker compose config --services failed" in result.stderr
    # compose's own error stays visible — the operator needs to read *why*.
    assert "required variable is missing" in result.stderr
    # ...and preflight still ends with its footer: a later `docker compose config --services |
    # tr` under `set -e`/`pipefail` used to abort the script here, footer-less.
    assert "preflight: FAILED" in result.stderr


def test_an_empty_service_list_is_a_failure_not_a_zero_megabyte_box(box: Box) -> None:
    box.extra_env = {"STUB_COMPOSE_EMPTY": "1"}
    box.write_env(compose_profiles="inventory")
    result = box.preflight()
    _memory_row_failed_closed(result)
    assert "listed no services" in result.stderr


def test_docker_ps_failing_is_a_failure_because_the_running_set_is_unknown(box: Box) -> None:
    box.extra_env = {"STUB_PS_FAILS": "1"}
    box.write_env(compose_profiles="inventory")
    result = box.preflight()
    _memory_row_failed_closed(result)
    assert "docker ps failed" in result.stderr


def test_an_unreadable_memtotal_is_a_failure_not_a_skipped_check(box: Box) -> None:
    box.meminfo.write_text("MemFree: 1 kB\n")
    box.write_env(compose_profiles="inventory")
    result = box.preflight()
    assert result.returncode != 0
    assert "could not read MemTotal" in result.stderr


def test_record_budget_reports_why_it_could_not_price_a_broken_compose_file(box: Box) -> None:
    box.extra_env = {"STUB_COMPOSE_FAILS": "1"}
    box.write_env(compose_profiles="inventory")
    result = box.run("remote.sh", "record-budget")
    assert "could not price the running box" in result.stderr
    assert "docker compose config --services failed" in result.stderr
    assert not (box.state / "edge-budget.last-ok").exists()


def test_restarting_and_paused_containers_are_priced_and_exited_ones_are_not(
    box: Box, budget: dict
) -> None:
    """A crash-looping container is resident between crashes and a paused one keeps every page;
    an exited one holds nothing. Pricing only `status=running` dropped the first two — exactly
    the containers a struggling box is most likely to be carrying."""
    box.running = _core(budget)
    box.restarting = {"operations"}
    box.paused = {"approval"}
    box.exited = {"employee"}
    box.write_env(compose_profiles="")  # none of the three is declared any more
    priced = _priced_mb(box.preflight())
    assert priced == expected_total_for_names(budget, _core(budget) | {"operations", "approval"})
    assert priced > expected_total_for_names(budget, _core(budget))


# -------------------------------------- round 3, M5: the ceiling has an upper bound


def _cap_mb(budget: dict, mem_total_mb: int = MEM_TOTAL_MB) -> int:
    return mem_total_mb * budget["host"]["max_accepted_percent"] // 100


def test_a_ceiling_above_the_cap_is_a_failure_naming_the_cap(box: Box, budget: dict) -> None:
    """The typo the cap exists to catch: 38940 for 3894 was a blank cheque."""
    total, mem_total_mb = _small_box_over_hard_line(budget, box)
    box.write_env(compose_profiles=_all_ten_csv(budget), accepted_mb=str(total * 10))
    result = box.preflight()
    assert result.returncode != 0
    assert f"EDGE_BUDGET_ACCEPTED_MB ({total * 10}M) is above the " in result.stderr
    assert (
        f"{budget['host']['max_accepted_percent']}% cap ({_cap_mb(budget, mem_total_mb)}M"
        in result.stderr
    )
    # ...and the rejected ceiling covers nothing: the deploy itself refuses too.
    assert _memory_failed(result)
    assert "was rejected" in result.stderr


def test_a_total_over_the_cap_is_refused_and_says_no_ceiling_can_cover_it(
    box: Box, budget: dict
) -> None:
    total = _ten_profile_total(budget)
    small_box_mb = total * 100 // (budget["host"]["max_accepted_percent"] + 10)  # total > cap
    box.meminfo.write_text(f"MemTotal:       {small_box_mb * 1024} kB\n")
    box.write_env(compose_profiles=_all_ten_csv(budget), accepted_mb=str(total))
    result = box.preflight()
    assert _memory_failed(result)
    assert total > _cap_mb(budget, small_box_mb)
    assert f"EDGE_BUDGET_ACCEPTED_MB ({total}M) is above the " in result.stderr


def test_a_ceiling_exactly_at_the_cap_is_accepted(box: Box, budget: dict) -> None:
    """125% of a box just big enough that the total sits under it."""
    total = _ten_profile_total(budget)
    cap_pct = budget["host"]["max_accepted_percent"]
    mem_mb = (total * 100 + cap_pct - 1) // cap_pct  # smallest box whose cap is >= total
    box.meminfo.write_text(f"MemTotal:       {mem_mb * 1024} kB\n")
    cap = _cap_mb(budget, mem_mb)
    assert cap >= total
    box.write_env(compose_profiles=_all_ten_csv(budget), accepted_mb=str(cap))
    result = box.preflight()
    assert not _memory_failed(result), result.stderr
    assert "OVER THE HARD BUDGET" in result.stderr


def test_slack_between_the_ceiling_and_the_total_is_reported(box: Box, budget: dict) -> None:
    total, _mem_total_mb = _small_box_over_hard_line(budget, box)
    box.write_env(compose_profiles=_all_ten_csv(budget), accepted_mb=str(total + 50))
    result = box.preflight()
    assert not _memory_failed(result), result.stderr
    assert f"is 50M above this deploy's total ({total}M)" in result.stdout


def test_a_ceiling_at_exactly_the_total_reports_no_slack(box: Box, budget: dict) -> None:
    total, _mem_total_mb = _small_box_over_hard_line(budget, box)
    box.write_env(compose_profiles=_all_ten_csv(budget), accepted_mb=str(total))
    result = box.preflight()
    assert not _memory_failed(result), result.stderr
    assert "above this deploy's total" not in result.stdout
    assert "set but not needed" not in result.stdout


def test_a_ceiling_this_deploy_does_not_need_is_called_out(box: Box, budget: dict) -> None:
    """A ceiling left in `.env` after the box was resized or a product de-listed silently
    pre-approves the next deploy that does go over."""
    box.write_env(compose_profiles="inventory", accepted_mb="4000")
    result = box.preflight()
    assert not _memory_failed(result)
    assert "EDGE_BUDGET_ACCEPTED_MB (4000M) is set but not needed" in result.stdout


# ------------------------------- round 3, LOW: COMPOSE_PROFILES=* and the exported list


def test_a_wildcard_profile_list_is_expanded_not_read_as_no_products(
    box: Box, budget: dict
) -> None:
    """`COMPOSE_PROFILES=*` works in docker compose and in ops-monitor. preflight's advisories
    match profiles one name at a time, where a literal `*` matches nothing — every sold product
    would have read as "no service will run for it" and refused a healthy deploy."""
    box.extra_env = {
        "STUB_POSTGRES": "1",
        "STUB_AVAILABLE": "operations,storefront,hrms",
        "STUB_ACTIVATED": "operations",
    }
    box.write_env(compose_profiles="*", accepted_mb=str(_ten_profile_total(budget)))
    result = box.preflight()
    assert "COMPOSE_PROFILES=* expanded to:" in result.stdout
    assert "no service will run for it" not in result.stderr
    assert "customers would lose access" not in result.stderr
    assert not _memory_failed(result), result.stderr
    assert _priced_mb(result) == _ten_profile_total(budget)


def test_a_wildcard_is_expanded_by_remote_sh_too(box: Box, budget: dict) -> None:
    """What compose is handed is the explicit list, not the `*` — the same list preflight judged,
    and the one ops-monitor (which reads `COMPOSE_PROFILES` from the environment compose passes
    it) derives its expected containers from."""
    box.write_env(compose_profiles="*")
    result = box.run("remote.sh", "record-budget")
    assert result.returncode == 0, result.stderr
    assert (box.state / "edge-budget.last-ok").read_text().strip() == str(
        _ten_profile_total(budget)
    )
    up = box.run("remote.sh", "up")
    assert up.returncode == 0, up.stderr
    handed = (box.root / "up.log.profiles").read_text().split()
    assert handed == [_all_ten_csv(budget)]


def test_remote_sh_always_exports_the_profile_list_it_resolved(box: Box, budget: dict) -> None:
    """A stray `COMPOSE_PROFILES` exported in the operator's own shell beats `--env-file`. When
    only EDGE_PROFILES caused an export, a stray value silently won and record-budget priced a
    different box than preflight had judged."""
    box.extra_env = {"COMPOSE_PROFILES": "hrms"}  # the stray one
    box.write_env(compose_profiles="inventory")
    result = box.run("remote.sh", "record-budget")
    assert result.returncode == 0, result.stderr
    assert (box.state / "edge-budget.last-ok").read_text().strip() == str(
        expected_total_mb(budget, {"inventory"})
    )


# ------------------------------------ round 3, LOW: RUN_SWEEPS_IN_API is loud when not true


def test_the_in_api_sweeps_being_switched_off_is_warned_about(box: Box) -> None:
    box.write_env(compose_profiles="")
    with_flag = box.edge / ".env"
    with_flag.write_text(with_flag.read_text() + "RUN_SWEEPS_IN_API=false\n")
    result = box.preflight()
    assert "RUN_SWEEPS_IN_API=false" in result.stderr
    assert "no separate cron container exists" in result.stderr


def test_the_in_api_sweeps_default_is_reported_ok(box: Box) -> None:
    box.write_env(compose_profiles="")
    result = box.preflight()
    assert "in-API sweeps enabled" in result.stdout


# ------------------------------------- round 3: how many containers `up` starts at once


def _up_limits(box: Box) -> list[str]:
    log = box.root / "up.log"
    return log.read_text().split() if log.exists() else []


def test_up_starts_four_containers_at_a_time_unless_told_otherwise(box: Box) -> None:
    result = box.run("remote.sh", "up")
    assert result.returncode == 0, result.stderr
    assert _up_limits(box) == ["4"]


def test_the_up_parallel_limit_can_be_set_in_the_environment(box: Box) -> None:
    box.extra_env = {"EDGE_UP_PARALLEL_LIMIT": "2"}
    assert box.run("remote.sh", "up").returncode == 0
    assert _up_limits(box) == ["2"]


def test_the_up_parallel_limit_can_be_set_in_dot_env_because_ssh_carries_no_environment(
    box: Box,
) -> None:
    """`make deploy` runs remote.sh over ssh: an operator's exported variable never arrives. The
    deploy that merges a batch of new containers onto a swapping box needs 2, and needs it to
    actually reach compose."""
    env_file = box.edge / ".env"
    env_file.write_text(env_file.read_text() + "EDGE_UP_PARALLEL_LIMIT=2\n")
    assert box.run("remote.sh", "up").returncode == 0
    assert _up_limits(box) == ["2"]


@pytest.mark.parametrize("value", ["0", "two", "-1", "2.5", ""])
def test_a_value_that_is_not_a_positive_whole_number_falls_back_to_four(
    box: Box, value: str
) -> None:
    box.extra_env = {"EDGE_UP_PARALLEL_LIMIT": value}
    assert box.run("remote.sh", "up").returncode == 0
    assert _up_limits(box) == ["4"]


# ---------- round 3, deploy blockers found by the environment audit: the Caddyfile site list and
# ---------- a `.env` that is sourced as shell


def _caddy_site_sed_expression() -> str:
    """The exact `sed` program preflight.sh runs to list the Caddyfile's site names — pulled out
    of the script text, so this tests what ships and not a copy of it."""
    text = (EDGE_SRC / "preflight.sh").read_text()
    m = re.search(r"sed -n '(s#\^https://[^']*)' caddy/Caddyfile", text)
    assert m, "preflight.sh no longer extracts Caddyfile site names with a `sed -n 's#^https://…'`"
    return m.group(1)


def _caddy_sites() -> list[str]:
    result = subprocess.run(
        ["sed", "-n", _caddy_site_sed_expression(), str(EDGE_SRC / "caddy/Caddyfile")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def test_the_caddy_site_list_never_contains_an_empty_name() -> None:
    """`https:// {` — the custom-domain on-demand-TLS catch-all — matched the old `[a-z0-9.-]*`
    as an EMPTY site name, so every preflight run failed with "the Caddyfile serves  but
    TLS_PLATFORM_HOSTS does not list it", whatever the box's `.env` said (commit ffaa682)."""
    sites = _caddy_sites()
    assert sites, "the expression found no site at all — it is broken the other way"
    assert "" not in sites


def test_the_caddyfile_catch_all_really_is_in_the_caddyfile() -> None:
    """The regression's premise: if `https:// {` ever leaves the Caddyfile this file's test above
    stops proving anything, and should be said so rather than pass vacuously."""
    caddyfile = (EDGE_SRC / "caddy/Caddyfile").read_text()
    assert re.search(r"^https:// \{$", caddyfile, re.M)


def test_every_caddy_site_is_listed_in_the_example_tls_platform_hosts() -> None:
    """A site the Caddyfile serves and `TLS_PLATFORM_HOSTS` does not list gets no certificate. The
    example is what a new box starts from. (`https://{$EDGE_HOST} {` is not matched by the
    expression on purpose — EDGE_HOST is listed by its own key — and the catch-all has no name.)"""
    listed = set()
    for line in (EDGE_SRC / ".env.example").read_text().splitlines():
        if line.startswith("TLS_PLATFORM_HOSTS="):
            listed = set(line.split("=", 1)[1].split(","))
    assert listed
    unlisted = [site for site in _caddy_sites() if site not in listed]
    assert not unlisted, f"Caddy serves {unlisted}, which .env.example's TLS_PLATFORM_HOSTS omits"


def test_preflight_no_longer_fails_on_the_catch_all_site(box: Box) -> None:
    box.write_env(compose_profiles="")
    result = box.preflight()
    assert "the Caddyfile serves  but" not in result.stderr
    assert "TLS_PLATFORM_HOSTS does not list it" not in result.stderr


def _append_to_env(box: Box, *lines: str) -> None:
    env_file = box.edge / ".env"
    env_file.write_text(env_file.read_text() + "\n".join(lines) + "\n")


def test_an_unquoted_value_with_whitespace_is_a_named_failure_not_exit_127(box: Box) -> None:
    """`.env` is sourced as shell: `EDGE_PROFILES=inventory, storefront` assigns `inventory,` and
    then runs `storefront` as a command — exit 127, "command not found", no FAILED footer."""
    box.write_env(compose_profiles="")
    _append_to_env(box, "EDGE_PROFILES=inventory, storefront", "OPS_ALERT_EMAIL=a and b")
    result = box.preflight()
    assert result.returncode == 1, (result.returncode, result.stderr)
    assert (
        "FAIL  EDGE_PROFILES in .env has an unquoted value containing whitespace" in result.stderr
    )
    assert (
        "FAIL  OPS_ALERT_EMAIL in .env has an unquoted value containing whitespace" in result.stderr
    )
    assert "preflight: FAILED" in result.stderr
    assert "command not found" not in result.stderr


def test_nothing_in_a_bad_env_line_is_executed(box: Box) -> None:
    """The check runs on the raw file BEFORE it is sourced: the words after the space must never
    reach the shell."""
    marker = box.root / "executed"
    box.write_env(compose_profiles="")
    _append_to_env(box, f"OPS_ALERT_EMAIL=a touch {marker}")
    result = box.preflight()
    assert result.returncode == 1
    assert not marker.exists()


def test_quoted_values_a_trailing_space_and_inline_comments_are_not_flagged(box: Box) -> None:
    box.write_env(compose_profiles="")
    _append_to_env(
        box,
        'OPS_ALERT_EMAIL="ops team <ops@opsgrids.test>"',
        "SENTRY_DSN='a b'",
        "EDGE_WEB_ROOT=/srv/opsgrids/web   ",
        "S3_PUBLIC_BUCKET=opsgrids-public # the public bucket",
        "  # an indented comment with = and spaces",
        "# COMMENTED_KEY=has spaces",
    )
    result = box.preflight()
    assert "unquoted value containing whitespace" not in result.stderr
    assert "command not found" not in result.stderr


def test_a_value_that_contains_a_hash_but_no_whitespace_is_left_alone(box: Box) -> None:
    """`KEY=abc#def` is one word to bash — a comment only starts after whitespace."""
    box.write_env(compose_profiles="")
    _append_to_env(box, "SENTRY_DSN=https://abc#def@sentry.example.test/1")
    assert "unquoted value containing whitespace" not in box.preflight().stderr


def test_windows_line_endings_are_a_named_failure_and_not_a_whitespace_complaint(
    box: Box,
) -> None:
    """Bash keeps the carriage return on the end of every value it sources (`EDGE_HOST` would be
    `edge.opsgrids.test\\r`) where docker compose strips it, so the two disagree about the box.
    Failed by name, before anything is sourced; and the CR is not read as "whitespace"."""
    box.write_env(compose_profiles="")
    env_file = box.edge / ".env"
    env_file.write_bytes(env_file.read_bytes().replace(b"\n", b"\r\n"))
    result = box.preflight()
    assert result.returncode == 1
    assert "FAIL  .env has Windows (CRLF) line endings" in result.stderr
    assert "unquoted value containing whitespace" not in result.stderr
    assert "preflight: FAILED" in result.stderr


def test_a_lowercase_assignment_is_checked_too(box: Box) -> None:
    """The shell runs `not_upper=has spaces` just as it runs an upper-case one."""
    box.write_env(compose_profiles="")
    _append_to_env(box, "site_note=has spaces")
    result = box.preflight()
    assert result.returncode == 1
    assert "FAIL  site_note in .env has an unquoted value containing whitespace" in result.stderr


# ---------------------------------------------- final fixup: reading `.env` the way bash does


def _dot_env(box: Box, text: str) -> None:
    env_file = box.edge / ".env"
    env_file.write_text(env_file.read_text() + text)


def test_remote_sh_reads_dot_env_the_way_bash_and_compose_do(box: Box) -> None:
    """LAST assignment wins and an unquoted value's inline ` # comment` is dropped — as when
    preflight sources the file. remote.sh used to take the FIRST line and keep the comment, so an
    operator's `COMPOSE_PROFILES=inventory # sold`, or a corrected line appended at the bottom,
    made `up` start a different profile list than preflight had judged, and
    `EDGE_UP_PARALLEL_LIMIT=2 # this deploy only` silently fell back to 4."""
    box.write_env(compose_profiles="inventory")
    env_file = box.edge / ".env"
    env_file.write_text(
        env_file.read_text().replace(
            "COMPOSE_PROFILES=inventory", "COMPOSE_PROFILES=inventory # sold"
        )
    )
    _dot_env(
        box,
        "COMPOSE_PROFILES=inventory,storefront # appended later\n"
        "EDGE_UP_PARALLEL_LIMIT=2 # this deploy only\n",
    )
    result = box.run("remote.sh", "up")
    assert result.returncode == 0, result.stderr
    assert (box.root / "up.log").read_text().split() == ["2"]
    assert (box.root / "up.log.profiles").read_text().split() == ["inventory,storefront"]
    # `up` says what it is about to do, so the operator can see it before compose does it.
    assert "up: starting 2 containers at a time, profiles: inventory,storefront" in result.stdout


def test_a_later_edge_profiles_line_and_a_comment_after_it_are_read_correctly(box: Box) -> None:
    box.write_env(compose_profiles="hrms", edge_profiles="hrms")
    _dot_env(box, 'EDGE_PROFILES="inventory,crm"  # onboarding\n')
    result = box.run("remote.sh", "up")
    assert result.returncode == 0, result.stderr
    assert (box.root / "up.log.profiles").read_text().split() == ["inventory,crm"]
    assert "profiles: EDGE_PROFILES set — running inventory,crm" in result.stdout


def test_a_comment_only_value_is_empty_not_the_comment(box: Box) -> None:
    """`KEY= # note` is an empty assignment in bash; the read must not return `#note`."""
    box.write_env(compose_profiles="")
    _dot_env(box, "EDGE_UP_PARALLEL_LIMIT= # unset on purpose\n")
    assert box.run("remote.sh", "up").returncode == 0
    assert (box.root / "up.log").read_text().split() == ["4"]


def test_assert_host_reads_the_last_edge_host_line_and_ignores_a_comment(box: Box) -> None:
    """The wrong-box interlock must agree with what preflight's shell sees: last line wins, and
    `EDGE_HOST=… # prod` is still that host."""
    box.write_env(compose_profiles="")
    _dot_env(box, "EDGE_HOST=other.example.test # moved\n")
    assert box.run("remote.sh", "assert-host", "other.example.test").returncode == 0
    refused = box.run("remote.sh", "assert-host", "edge.opsgrids.test")
    assert refused.returncode == 2
    assert "REFUSING" in refused.stderr


# ------------------------------------------- final fixup: the catalogue advisory with 2+ products


def test_several_sold_products_are_all_recognised_by_the_catalogue_advisory(box: Box) -> None:
    """`case " $available " in` against newline-separated psql output only ever matched the first
    key: with two or more sold products the advisory called sold products "not sold in the
    catalogue — safe to trim", to an owner deciding what to de-list."""
    box.extra_env = {
        "STUB_POSTGRES": "1",
        "STUB_AVAILABLE": "operations,storefront,hrms",
        "STUB_ACTIVATED": "operations",
    }
    box.write_env(compose_profiles="inventory,storefront,hrms")
    result = box.preflight()
    assert "catalogue does not sell" not in result.stderr
    assert "not sold in the catalogue" not in result.stderr


def test_a_profile_the_catalogue_does_not_sell_is_still_reported_beside_sold_ones(
    box: Box,
) -> None:
    """The fix must not turn the advisory off: `crm` runs but is not `available`."""
    box.extra_env = {
        "STUB_POSTGRES": "1",
        "STUB_AVAILABLE": "operations,storefront",
        "STUB_ACTIVATED": "",
    }
    box.write_env(compose_profiles="inventory,storefront,crm")
    result = box.preflight()
    assert "profile 'crm' runs but the catalogue does not sell 'crm'" in result.stderr
    assert "profile 'inventory' runs but" not in result.stderr
    assert "profile 'storefront' runs but" not in result.stderr


# ------------------------------------------- final fixup: ceiling and MemTotal parsing


@pytest.mark.parametrize("value", ["04600", "04589", "00", "99999999999999999999"])
def test_a_ceiling_with_a_leading_zero_or_twenty_digits_fails_cleanly(
    box: Box, budget: dict, value: str
) -> None:
    """`04600` is octal to bash arithmetic and `04589` is "value too great for base"; twenty digits
    overflow to a negative number. All three used to reach `[ -gt ]` and either die with an
    arithmetic error or print nonsense (`-2149M`)."""
    box.write_env(compose_profiles=_all_ten_csv(budget), accepted_mb=value)
    result = box.preflight()
    assert "value too great" not in result.stderr and "integer expression" not in result.stderr
    assert "-2149M" not in result.stdout
    assert "FAIL  EDGE_BUDGET_ACCEPTED_MB" in result.stderr
    assert "preflight: FAILED" in result.stderr


def test_a_leading_zero_ceiling_names_the_rule(box: Box, budget: dict) -> None:
    box.write_env(compose_profiles=_all_ten_csv(budget), accepted_mb="04581")
    assert "no sign, unit or leading zero" in box.preflight().stderr


@pytest.mark.parametrize("text", ["MemTotal: 0 kB\n", "MemTotal: abc kB\n", "MemTotal: -5 kB\n"])
def test_a_zero_or_non_numeric_memtotal_is_the_could_not_read_row(box: Box, text: str) -> None:
    box.meminfo.write_text(text)
    box.write_env(compose_profiles="inventory")
    result = box.preflight()
    assert "could not read MemTotal" in result.stderr
    assert "preflight: FAILED" in result.stderr


def test_a_missing_memory_budget_file_fails_preflight_instead_of_skipping_the_check(
    box: Box,
) -> None:
    """The gate fails closed everywhere else; a budget it cannot read is not one it may skip."""
    (box.edge / "memory-budget.json").unlink()
    box.write_env(compose_profiles="inventory")
    result = box.preflight()
    assert result.returncode == 1
    assert "FAIL  no memory-budget.json — the memory budget cannot be judged" in result.stderr


# ------------------------------------------- final fixup: the nits that were trivial


@pytest.mark.parametrize(
    "line",
    [
        "OPS_ALERT_EMAIL=a;touch{marker}",
        "OPS_ALERT_EMAIL=a&b",
        "OPS_ALERT_EMAIL=a|b",
        "OPS_ALERT_EMAIL=$(touch{marker})",
        "OPS_ALERT_EMAIL=`touch{marker}`",
        "OPS_ALERT_EMAIL=<a@b.test>",
        "OPS_ALERT_EMAIL=(a)",
    ],
)
def test_an_unquoted_shell_metacharacter_is_a_named_failure_and_nothing_runs(
    box: Box, line: str
) -> None:
    marker = box.root / "executed"
    box.write_env(compose_profiles="")
    _dot_env(box, line.format(marker=marker) + "\n")
    result = box.preflight()
    assert result.returncode == 1
    assert (
        "FAIL  OPS_ALERT_EMAIL in .env has an unquoted value containing a shell metacharacter"
        in (result.stderr)
    )
    assert not marker.exists()


def test_a_quoted_shell_metacharacter_is_fine(box: Box) -> None:
    box.write_env(compose_profiles="")
    _dot_env(box, 'OPS_ALERT_EMAIL="ops; team <ops@opsgrids.test>"\n')
    assert "shell metacharacter" not in box.preflight().stderr


@pytest.mark.parametrize(
    "line",
    ['SENTRY_DSN="a" b', "SENTRY_DSN='a' b", 'SENTRY_DSN="a"b'],
)
def test_text_after_a_closing_quote_is_a_named_failure(box: Box, line: str) -> None:
    box.write_env(compose_profiles="")
    _dot_env(box, line + "\n")
    result = box.preflight()
    assert result.returncode == 1
    assert "FAIL  SENTRY_DSN in .env has text after its closing quote" in result.stderr


def test_a_comment_after_a_closing_quote_is_fine(box: Box) -> None:
    box.write_env(compose_profiles="")
    _dot_env(box, 'SENTRY_DSN="a b"  # the dsn\n')
    assert "closing quote" not in box.preflight().stderr


def test_export_lines_are_checked_like_any_other_assignment(box: Box) -> None:
    box.write_env(compose_profiles="")
    _dot_env(box, "export OPS_ALERT_EMAIL=a b\n")
    result = box.preflight()
    assert result.returncode == 1
    assert "FAIL  OPS_ALERT_EMAIL in .env has an unquoted value containing whitespace" in (
        result.stderr
    )


def test_an_unknown_profile_name_is_a_named_failure(box: Box) -> None:
    """`COMPOSE_PROFILES=hrm` enables nothing, silently: priced, judged and started as core only."""
    box.write_env(compose_profiles="inventory,hrm")
    result = box.preflight()
    assert result.returncode == 1
    assert "FAIL  profile 'hrm' (from EDGE_PROFILES/COMPOSE_PROFILES) is not defined" in (
        result.stderr
    )
    assert "profile 'inventory' (from" not in result.stderr


def test_every_defined_profile_name_passes_the_name_check(box: Box, budget: dict) -> None:
    box.write_env(
        compose_profiles=_all_ten_csv(budget), accepted_mb=str(_ten_profile_total(budget))
    )
    assert "is not defined in compose.prod.yml" not in box.preflight().stderr


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "on"])
def test_the_in_api_sweep_flag_is_read_the_way_pydantic_reads_a_bool(box: Box, value: str) -> None:
    box.write_env(compose_profiles="")
    _dot_env(box, f"RUN_SWEEPS_IN_API={value}\n")
    result = box.preflight()
    assert "in-API sweeps enabled" in result.stdout
    assert "sweeps are OFF" not in result.stderr


@pytest.mark.parametrize("value", ["false", "False", "0", "no", "off", "maybe"])
def test_anything_else_is_still_warned_about_as_off(box: Box, value: str) -> None:
    box.write_env(compose_profiles="")
    _dot_env(box, f"RUN_SWEEPS_IN_API={value}\n")
    assert "sweeps are OFF" in box.preflight().stderr
