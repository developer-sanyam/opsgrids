"""Pins `infrastructure/edge/ci-plan.sh`'s path-diff logic — the second half of the same
"what changed, and what does it need to deploy" family remote.sh's rebuild rule belongs to,
and equally untested before this file existed. Runs the real script as a subprocess in a
throwaway git repo rather than reimplementing its rules.
"""

from __future__ import annotations

import json
import os
import subprocess

from conftest import CI_PLAN, GitRepo

ALL_APPS = [
    "buyer",
    "dashboard",
    "operations",
    "hrms",
    "crm",
    "pulseboard",
    "projects",
    "support",
    "marketing",
    "finance",
    "itsm",
    "storefront",
    "engage",
]

BASELINE_FILES = {
    "services/checkout/app.py": "v1\n",
    "frontend/opsgrids-ui/src/index.ts": "v1\n",
    "frontend/product-hrms/src/index.ts": "v1\n",
    "frontend/product-storefront/src/index.ts": "v1\n",
    "frontend/pnpm-lock.yaml": "v1\n",
    "docs/backlog.md": "v1\n",
    "e2e/journey.py": "v1\n",
    ".github/workflows/ci.yml": "v1\n",
}


def run_ci_plan(repo: GitRepo, base: str) -> dict[str, object]:
    env = os.environ.copy()
    env["BASE"] = base
    env.pop("GITHUB_OUTPUT", None)
    result = subprocess.run(
        ["bash", str(CI_PLAN), "diff"],
        cwd=repo.path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return _parse(result.stdout)


def _parse(stdout: str) -> dict[str, object]:
    plan: dict[str, object] = {}
    for line in stdout.splitlines():
        if line.startswith("services="):
            plan["services"] = line.split("=", 1)[1]
        elif line.startswith("apps="):
            plan["apps"] = json.loads(line.split("=", 1)[1])
    return plan


def _baseline(git_repo: GitRepo) -> str:
    return git_repo.initial_commit(BASELINE_FILES, "baseline")


def test_backend_only_change_deploys_services_and_no_apps(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    git_repo.commit_from(base, {"services/checkout/app.py": "v2\n"}, "edit checkout")
    plan = run_ci_plan(git_repo, base)
    assert plan["services"] == "true"
    assert plan["apps"] == []


def test_frontend_docs_and_e2e_only_does_not_deploy_services(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    git_repo.commit_from(
        base,
        {"docs/backlog.md": "v2\n", "e2e/journey.py": "v2\n"},
        "docs and e2e only",
    )
    plan = run_ci_plan(git_repo, base)
    assert plan["services"] == "false"
    assert plan["apps"] == []


def test_shared_ui_package_change_plans_every_app(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    git_repo.commit_from(base, {"frontend/opsgrids-ui/src/index.ts": "v2\n"}, "edit shared ui")
    plan = run_ci_plan(git_repo, base)
    assert plan["services"] == "false"
    assert plan["apps"] == ALL_APPS


def test_lockfile_change_plans_every_app(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    git_repo.commit_from(base, {"frontend/pnpm-lock.yaml": "v2\n"}, "bump a frontend dep")
    plan = run_ci_plan(git_repo, base)
    assert plan["apps"] == ALL_APPS


def test_single_app_directory_plans_only_that_app(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    git_repo.commit_from(base, {"frontend/product-hrms/src/index.ts": "v2\n"}, "edit hrms app")
    plan = run_ci_plan(git_repo, base)
    assert plan["services"] == "false"
    assert plan["apps"] == ["hrms"]


def test_storefront_directory_plans_both_its_apps(git_repo: GitRepo) -> None:
    """buyer and storefront are two entry points of one package (product-storefront)."""
    base = _baseline(git_repo)
    git_repo.commit_from(
        base, {"frontend/product-storefront/src/index.ts": "v2\n"}, "edit storefront app"
    )
    plan = run_ci_plan(git_repo, base)
    assert plan["apps"] == ["buyer", "storefront"]


def test_no_usable_base_ref_deploys_everything(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    assert base  # the baseline commit exists; only the BASE ref given below is unusable
    # A base this clone has never heard of — the "rewritten history / new box" case.
    env = os.environ.copy()
    env["BASE"] = "0000000000000000000000000000000000000000"
    env.pop("GITHUB_OUTPUT", None)
    result = subprocess.run(
        ["bash", str(CI_PLAN), "diff"],
        cwd=git_repo.path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    plan = _parse(result.stdout)
    assert plan["services"] == "true"
    assert plan["apps"] == ALL_APPS


def test_head_to_head_deploys_nothing(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    plan = run_ci_plan(git_repo, base)
    assert plan["services"] == "false"
    assert plan["apps"] == []
