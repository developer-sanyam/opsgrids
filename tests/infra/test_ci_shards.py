"""Pins how CI splits the service tests: `tools/ci_shards.py`, its shell plumbing, and the
workflow and Makefile settings that must agree with it.

The failure all of this guards against is silent. A shard plan that drops a test file, or two
parts of one service that share a database, does not break a build — the pipeline stays green
while a suite stops running, or goes red at random because two parts truncated each other's
tables. So these tests drive the real planner against throwaway trees it cannot confuse with the
repository, and then check the repository against it.

No database, no network: the planner reads file names and a JSON file.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
MAKEFILE = REPO_ROOT / "Makefile"


def _load() -> ModuleType:
    path = REPO_ROOT / "tools" / "ci_shards.py"
    spec = importlib.util.spec_from_file_location("ci_shards", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ci_shards"] = module  # dataclasses resolve their annotations through sys.modules
    spec.loader.exec_module(module)
    return module


ci_shards = _load()


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty `services/` the planner is pointed at instead of the repository's."""
    (tmp_path / "services").mkdir()
    monkeypatch.setattr(ci_shards, "ROOT", tmp_path)
    monkeypatch.setattr(ci_shards, "SERVICES", tmp_path / "services")
    monkeypatch.setattr(ci_shards, "DURATIONS", tmp_path / "durations.json")
    return tmp_path


def _service(tree: Path, name: str, files: dict[str, float]) -> None:
    """A service with test files and recorded durations for them."""
    tests = tree / "services" / name / "tests"
    tests.mkdir(parents=True)
    (tests / "__init__.py").write_text("")
    (tests / "conftest.py").write_text("")
    for f in files:
        (tests / f).write_text("")
    path = tree / "durations.json"
    existing = json.loads(path.read_text())["files"] if path.exists() else {}
    existing.update({f"{name}/tests/{f}": secs for f, secs in files.items()})
    path.write_text(json.dumps({"files": existing}))


def _all_files(plan: list[list[Any]]) -> list[tuple[str, str]]:
    return sorted((it.service, f) for shard in plan for it in shard for f in it.files)


# ---------------------------------------------------------------- the planner on a toy tree


def test_a_service_that_outweighs_half_a_share_is_cut_into_parts(tree: Path) -> None:
    _service(tree, "big", {f"test_{i}.py": 100.0 for i in range(8)})
    _service(tree, "small", {"test_a.py": 10.0})
    items = ci_shards.build_items(2)
    big = [it for it in items if it.service == "big"]
    assert len(big) == 2  # 800s on two shards: each share is ~405s, half a share is ~200s
    assert {it.parts for it in big} == {2}
    assert next(it for it in items if it.service == "small").parts == 1


def test_every_test_file_lands_in_exactly_one_item(tree: Path) -> None:
    _service(tree, "big", {f"test_{i}.py": float(10 + i) for i in range(11)})
    _service(tree, "mid", {f"test_m{i}.py": 30.0 for i in range(3)})
    _service(tree, "tiny", {"test_t.py": 1.0})
    plan = ci_shards.plan(3)
    on_disk = sorted(
        (s, f) for s in ("big", "mid", "tiny") for f in ci_shards.service_test_files(s)
    )
    assert _all_files(plan) == on_disk


def test_two_parts_of_one_service_never_share_a_shard(tree: Path) -> None:
    # The service fixtures TRUNCATE their schema before every test; two parts in one database
    # would do it to each other mid-test.
    _service(tree, "big", {f"test_{i}.py": 50.0 for i in range(20)})
    _service(tree, "other", {"test_o.py": 5.0})
    for shards in (2, 3, 4, 5):
        for shard in ci_shards.plan(shards):
            services = [it.service for it in shard]
            assert len(services) == len(set(services))


def test_a_part_count_above_the_shard_count_is_impossible_by_construction(tree: Path) -> None:
    _service(tree, "big", {f"test_{i}.py": 500.0 for i in range(30)})
    assert max(it.parts for it in ci_shards.build_items(4)) <= 4


def test_the_plan_is_the_same_every_time(tree: Path) -> None:
    _service(tree, "big", {f"test_{i}.py": float(7 * i % 13 + 1) for i in range(25)})
    _service(tree, "mid", {f"test_{i}.py": 9.0 for i in range(6)})
    first = [[it.name for it in shard] for shard in ci_shards.plan(4)]
    second = [[it.name for it in shard] for shard in ci_shards.plan(4)]
    assert first == second


def test_a_file_missing_from_the_durations_still_runs(tree: Path) -> None:
    _service(tree, "svc", {"test_known.py": 20.0})
    (tree / "services/svc/tests/test_brand_new.py").write_text("")
    assert ("svc", "tests/test_brand_new.py") in _all_files(ci_shards.plan(2))
    assert ci_shards.verify(2) == []


def test_verify_names_a_dropped_file(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _service(tree, "svc", {"test_a.py": 5.0, "test_b.py": 5.0})
    real = ci_shards.assign

    def dropping(items: list[Any], shards: int) -> list[list[Any]]:
        plan = real(items, shards)
        for shard in plan:
            for i, it in enumerate(shard):
                shard[i] = type(it)(
                    it.service,
                    it.part,
                    it.parts,
                    it.files[1:],
                    it.seconds,
                )
        return plan

    monkeypatch.setattr(ci_shards, "assign", dropping)
    problems = ci_shards.verify(2)
    assert any("svc" in p and "dropped" in p for p in problems)


def test_a_test_file_name_with_a_space_is_refused(tree: Path) -> None:
    _service(tree, "svc", {"test_a b.py": 5.0})
    assert any("space" in p for p in ci_shards.verify(2))


def test_the_first_shard_is_handed_less_because_it_also_runs_libs_and_smoke(tree: Path) -> None:
    for name in ("a", "b", "c", "d"):
        _service(tree, name, {"test_x.py": 100.0})
    shard1 = sum(it.seconds for it in ci_shards.plan(4)[0])
    assert shard1 <= max(sum(it.seconds for it in s) for s in ci_shards.plan(4)[1:])


def test_a_part_names_its_files_and_the_rest_name_theirs(tree: Path) -> None:
    _service(tree, "big", {f"test_{i}.py": 100.0 for i in range(8)})
    _service(tree, "small", {"test_a.py": 10.0})
    parts = [it for it in ci_shards.build_items(2) if it.service == "big"]
    listed = sorted(f for it in parts for f in ci_shards.item_files(it.name))
    assert listed == ci_shards.service_test_files("big")
    assert ci_shards.item_files("small") == ["tests/test_a.py"]


def test_an_item_name_that_is_not_one_is_refused(tree: Path) -> None:
    _service(tree, "svc", {"test_a.py": 1.0})
    for bad in ("svc:0/2", "svc:3/2", "svc:1", "../etc", "nope"):
        with pytest.raises(SystemExit):
            ci_shards.item_files(bad)


# ---------------------------------------------------------------- the affected filter


def test_needed_shards_always_include_the_first(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("a", "b", "c", "d"):
        _service(tree, name, {"test_x.py": 100.0})
    monkeypatch.setenv("AFFECTED_ALL", "false")
    monkeypatch.setenv("AFFECTED_SERVICES", "d")
    needed = ci_shards.shards_needed(4)
    assert 1 in needed
    # ...and the shard that actually holds `d` is there too, and a shard with nothing is not.
    holder = next(
        i + 1 for i, s in enumerate(ci_shards.plan(4)) if any(x.service == "d" for x in s)
    )
    assert holder in needed
    assert len(needed) < 4


def test_with_no_filter_every_shard_is_needed(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("a", "b", "c"):
        _service(tree, name, {"test_x.py": 100.0})
    monkeypatch.delenv("AFFECTED_ALL", raising=False)
    monkeypatch.delenv("AFFECTED_SERVICES", raising=False)
    assert ci_shards.shards_needed(3) == [1, 2, 3]


def test_an_empty_affected_list_falls_back_to_every_shard(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The same default-safe rule as tools/filter-affected.sh.
    for name in ("a", "b"):
        _service(tree, name, {"test_x.py": 100.0})
    monkeypatch.setenv("AFFECTED_ALL", "false")
    monkeypatch.setenv("AFFECTED_SERVICES", "")
    assert ci_shards.shards_needed(2) == [1, 2]


# ---------------------------------------------------------------- refreshing from junit


def test_junit_seconds_land_on_the_test_file_that_ran(tree: Path) -> None:
    _service(tree, "svc", {"test_a.py": 1.0, "test_b.py": 1.0})
    junit = tree / "junit"
    junit.mkdir()
    (junit / "svc.xml").write_text(
        "<testsuites><testsuite>"
        '<testcase classname="services.svc.tests.test_a" name="t1" time="1.5"/>'
        '<testcase classname="services.svc.tests.test_a.TestX" name="t2" time="2.0"/>'
        '<testcase classname="services.svc.tests.test_b" name="t3" time="4.0"/>'
        "</testsuite></testsuites>"
    )
    seconds = ci_shards.read_junit([junit])
    assert seconds == {"svc/tests/test_a.py": 3.5, "svc/tests/test_b.py": 4.0}


def test_a_testcase_that_is_not_under_services_is_ignored(tree: Path) -> None:
    _service(tree, "svc", {"test_a.py": 1.0})
    junit = tree / "junit"
    junit.mkdir()
    (junit / "x.xml").write_text(
        '<testsuite><testcase classname="elsewhere.test_z" name="t" time="9"/></testsuite>'
    )
    assert ci_shards.read_junit([junit]) == {}


# ---------------------------------------------------------------- the repository itself


def test_the_repository_plan_is_sound_at_the_configured_shard_count() -> None:
    shards = _workflow_test_shards()
    assert ci_shards.verify(shards) == []


def test_every_shard_of_the_repository_plan_has_work() -> None:
    shards = _workflow_test_shards()
    assert all(ci_shards.plan(shards))


def test_the_durations_file_names_only_files_that_exist() -> None:
    assert ci_shards.stale_durations() == []


def test_a_shard_cannot_hold_a_dominant_share() -> None:
    # The reason items exist: no one runner should be handed most of the suite. If the durations
    # are re-recorded and a single item grows past a shard's fair share, this says so.
    shards = _workflow_test_shards()
    plan = ci_shards.plan(shards)
    loads = [sum(it.seconds for it in shard) for shard in plan]
    assert max(loads) <= 1.35 * (sum(loads) / shards), loads


# ---------------------------------------------------------------- workflow and Makefile agree


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text())


def _workflow_test_shards() -> int:
    return int(_workflow()["env"]["TEST_SHARDS"])


def test_the_makefile_default_matches_the_workflows_shard_count() -> None:
    match = re.search(r"^TEST_SHARDS \?= (\d+)$", MAKEFILE.read_text(), re.MULTILINE)
    assert match, "Makefile must set TEST_SHARDS ?= <n>"
    assert int(match.group(1)) == _workflow_test_shards()


def test_the_tests_job_takes_its_matrix_and_shard_count_from_the_changes_job() -> None:
    jobs = _workflow()["jobs"]
    tests = jobs["tests"]
    assert "needs.changes.outputs.test_shards" in str(tests["strategy"]["matrix"]["shard"])
    assert "needs.changes.outputs.test_shard_count" in str(tests["env"]["SHARDS"])
    assert jobs["changes"]["outputs"]["test_shard_count"]


def test_a_push_to_main_is_never_cancelled_and_never_queued_behind_another() -> None:
    concurrency = _workflow()["concurrency"]
    assert "pull_request" in str(concurrency["cancel-in-progress"])
    group = str(concurrency["group"])
    # main's group is its own commit, so two pushes to main never share one.
    assert "github.sha" in group
    assert "github.ref" in group


def test_every_frontend_package_is_in_exactly_one_test_and_build_leg() -> None:
    legs = _workflow()["jobs"]["frontend-tests"]["strategy"]["matrix"]["include"]
    owned = [p.removeprefix("./") for leg in legs for p in str(leg["packages"]).split()]
    on_disk = sorted(
        p.parent.name for p in (REPO_ROOT / "frontend").glob("*/package.json") if p.parent.is_dir()
    )
    assert sorted(owned) == on_disk, "a package is in no leg, or in two"


def test_the_frontend_filter_is_empty_by_default() -> None:
    # An unset FRONTEND_FILTER must mean every package: that is what the gate and every local
    # run depend on.
    assert re.search(r"^FRONTEND_FILTER \?=\s*$", MAKEFILE.read_text(), re.MULTILINE)


# ---------------------------------------------------------------- shell plumbing


def test_fan_out_runs_an_item_in_its_services_directory_and_names_it(tmp_path: Path) -> None:
    (tmp_path / "services/svc/tests").mkdir(parents=True)
    result = subprocess.run(
        [
            str(REPO_ROOT / "tools/fan-out.sh"),
            "--stdin",
            "1",
            "tests",
            "ok-empty",
            "sh",
            "-c",
            'echo "item=$FAN_OUT_ITEM dir=$(basename "$PWD")" >&2; exit 1',
        ],
        input="svc:2/3\nsvc\n",
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1  # the command failed on purpose, so its output is printed
    assert "→ svc:2/3 FAILED" in result.stdout
    assert "item=svc:2/3 dir=svc" in result.stdout
    assert "item=svc dir=svc" in result.stdout


def test_the_affected_filter_keeps_an_item_by_its_service_name() -> None:
    env = {**os.environ, "AFFECTED_ALL": "false", "AFFECTED_SERVICES": "operations auth"}
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "tools/filter-affected.sh")],
        input="operations:2/5\nbilling\nauth\noperations-x:1/2\n",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.split() == ["operations:2/5", "auth"]


def test_the_postgres_tuning_script_refuses_to_run_outside_ci() -> None:
    # fsync off on a database anyone cares about loses data. The script must say no before it
    # opens a connection (there is none to open here).
    env = {k: v for k, v in os.environ.items() if k != "CI"}
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "tools/ci-postgres-tune.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "refusing to run outside CI" in result.stderr


# ---------------------------------------------------------------- the plan the jobs share


def test_needed_refuses_a_tree_it_cannot_plan(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A checkout that missed the services' tests would otherwise yield an empty-looking plan and
    # a matrix of one shard, and every other shard would simply never run.
    assert ci_shards.main(["ci_shards.py", "needed", "3"]) == 1
    assert "no work" in capsys.readouterr().err


def test_the_plan_digest_follows_the_tree(tree: Path) -> None:
    _service(tree, "svc", {"test_a.py": 5.0})
    before = ci_shards.digest(2)
    assert ci_shards.digest(2) == before  # stable
    (tree / "services/svc/tests/test_b.py").write_text("")
    assert ci_shards.digest(2) != before  # a file the other job did not see


def test_a_shard_whose_plan_differs_from_the_changes_jobs_refuses_to_run() -> None:
    env = {**os.environ, "TEST_PLAN_DIGEST": "0000000000000000"}
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "tools/test-shard.sh"), "1", str(_workflow_test_shards()), "1"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "differs from the changes job" in result.stderr
