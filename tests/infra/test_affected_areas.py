"""Pins `tools/affected-areas.sh`'s path classification — the logic
`.github/workflows/ci.yml`'s `changes` job delegates to rather than inlining, so it can be
tested like `infrastructure/edge/ci-plan.sh`'s path-diff logic is (test_ci_plan.py): run
the real script as a subprocess, never a reimplementation of its rules.

The workflow itself still owns the two cases this script never sees: a non-pull_request
event, and a pull request whose file list came back empty from the API. Both mean "run
everything" and are decided before the script is even invoked — this file tests what the
script does with a *non-empty* list of changed paths, plus its own fallback for the
degenerate case of getting none.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AFFECTED_AREAS = REPO_ROOT / "tools/affected-areas.sh"


def run(files: list[str]) -> dict[str, str]:
    stdin = "".join(f"{f}\n" for f in files)
    result = subprocess.run(
        ["bash", str(AFFECTED_AREAS)],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    )
    plan: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        plan[key] = value
    return plan


def test_a_single_services_directory_is_trusted_narrowly() -> None:
    plan = run(["services/auth/app/main.py", "services/auth/tests/test_main.py"])
    assert plan == {
        "backend": "true",
        "frontend": "false",
        "services_all": "false",
        "services_list": "auth",
    }


def test_two_services_directories_both_land_in_the_list() -> None:
    plan = run(["services/auth/app/main.py", "services/billing/app/models.py"])
    assert plan["services_all"] == "false"
    assert plan["services_list"] == "auth billing"


def test_duplicate_paths_in_one_service_do_not_duplicate_the_name() -> None:
    plan = run(["services/auth/app/main.py", "services/auth/app/config.py"])
    assert plan["services_list"] == "auth"


def test_frontend_only_change_does_not_touch_backend_or_services() -> None:
    plan = run(["frontend/product-crm/src/App.tsx"])
    assert plan["backend"] == "false"
    assert plan["frontend"] == "true"
    assert plan["services_list"] == ""


def test_a_shared_library_change_falls_back_to_every_service() -> None:
    # libs/platform-core is every service's dependency — not safe to narrow.
    plan = run(["libs/platform-core/src/platform_core/db.py"])
    assert plan["backend"] == "true"
    assert plan["services_all"] == "true"


def test_root_config_falls_back_to_every_service() -> None:
    plan = run(["services/auth/app/main.py", "Makefile"])
    assert plan["services_all"] == "true"


def test_the_workflow_file_itself_falls_back_to_everything() -> None:
    plan = run([".github/workflows/ci.yml"])
    assert plan["backend"] == "true"
    assert plan["frontend"] == "true"
    assert plan["services_all"] == "true"


def test_a_change_to_this_script_itself_does_not_narrow_its_own_run() -> None:
    # tools/ is root tooling, not a services/<name>/ path, so it takes the fail-open branch
    # like any other unrecognised path — a pull request cannot use a change to this script
    # to quietly shrink what that same pull request gets tested with.
    plan = run(["tools/affected-areas.sh"])
    assert plan["services_all"] == "true"


def test_attendance_change_also_runs_approvals_cross_service_suite() -> None:
    # services/approval/tests/test_attendance_round_trip.py reaches into attendance's
    # schema on purpose (see the `test-services` comment in the Makefile) — it is the
    # suite that actually proves the boundary holds, so it must run too.
    plan = run(["services/attendance/app/consumers.py"])
    assert plan["services_list"] == "approval attendance"


def test_employee_change_also_runs_approvals_cross_service_suite() -> None:
    plan = run(["services/employee/app/models.py"])
    assert plan["services_list"] == "approval employee"


def test_approval_changing_on_its_own_does_not_pull_in_attendance_or_employee() -> None:
    # The dependency is one-directional: approval's tests reach into the other two, not
    # the other way around, so only approval itself needs to run.
    plan = run(["services/approval/app/services.py"])
    assert plan["services_list"] == "approval"


def test_a_file_directly_under_services_with_no_subpath_falls_back() -> None:
    # Not services/<name>/<something> — one path segment short of the shape this script
    # trusts, so it takes the fail-open branch rather than guess.
    plan = run(["services/README.md"])
    assert plan["services_all"] == "true"


def test_no_input_at_all_runs_everything_rather_than_nothing() -> None:
    plan = run([])
    assert plan["backend"] == "true"
    assert plan["frontend"] == "true"
    assert plan["services_all"] == "true"


def test_mixed_backend_and_frontend_change_sets_both_and_still_narrows_services() -> None:
    plan = run(["services/auth/app/main.py", "frontend/product-crm/src/App.tsx"])
    assert plan["backend"] == "true"
    assert plan["frontend"] == "true"
    assert plan["services_all"] == "false"
    assert plan["services_list"] == "auth"
