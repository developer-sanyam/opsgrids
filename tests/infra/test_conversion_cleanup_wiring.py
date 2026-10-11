"""The edge wiring of the aborted-conversion cleanup (CRM R2.4), statically.

`CRM_CONVERSION_CLEANUP_MODE` reaches deal and customer with a `log_only` default (nothing is
deleted until the owner sets `on` in the box `.env`), the key stays out of `.env.example` (a key
there becomes a preflight prerequisite), deal's consumer is folded into its API container with a
`deal-worker` rollback behind `split-workers`, and the budget prices the fold. No database, no
broker.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
COMPOSE = EDGE_DIR / "compose.prod.yml"
BUDGET_FILE = EDGE_DIR / "memory-budget.json"

sys.path.insert(0, str(EDGE_DIR / "lib"))
from memory_budget_totals import expected_total_mb, load_budget  # noqa: E402

TEN = {
    "storefront",
    "inventory",
    "hrms",
    "crm",
    "pulseboard",
    "projects",
    "support",
    "marketing",
    "finance",
    "itsm",
}


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_x_service_env_carries_the_mode_with_the_log_only_default() -> None:
    env = _compose()["x-service-env"]
    assert env["CRM_CONVERSION_CLEANUP_MODE"] == "${CRM_CONVERSION_CLEANUP_MODE:-log_only}"


def test_deal_and_customer_settings_declare_the_field_with_the_log_only_default() -> None:
    for service in ("deal", "customer"):
        result = subprocess.run(
            [
                "uv",
                "run",
                "python",
                "-c",
                "from app.config import Settings; "
                "print(Settings.model_fields['crm_conversion_cleanup_mode'].default)",
            ],
            cwd=REPO_ROOT / "services" / service,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "log_only", service


def test_the_env_example_does_not_name_the_key() -> None:
    assert "CRM_CONVERSION_CLEANUP_MODE" not in (EDGE_DIR / ".env.example").read_text()


def test_deal_worker_exists_behind_split_workers_with_the_worker_command() -> None:
    worker = _compose()["services"]["deal-worker"]
    assert worker["profiles"] == ["split-workers"]
    assert worker["command"] == ["./bin/worker"]
    assert worker["image"] == "opsgrids-edge/deal"


def test_the_budget_prices_deal_with_worker_and_its_rollback_under_split_workers() -> None:
    containers = load_budget(BUDGET_FILE)["containers"]
    assert containers["deal"]["kind"] == "api_with_worker"
    assert containers["deal"]["profile"] == "crm"
    assert containers["deal-worker"]["kind"] == "background"
    assert containers["deal-worker"]["profile"] == "split-workers"


def test_the_compose_comment_no_longer_lists_deal_as_ignoring_the_fold() -> None:
    text = COMPOSE.read_text()
    comment = text[: text.index("  RUN_CONSUMERS_IN_API:")].rsplit("\n\n", 1)[-1]
    ignore_list = comment[comment.index("`run_consumers_in_api`") :]
    assert "`deal`" not in ignore_list


def test_the_budget_totals_are_this_releases_own() -> None:
    budget = load_budget(BUDGET_FILE)
    assert expected_total_mb(budget, {"crm"}) == 2068
    assert expected_total_mb(budget, TEN) == 3748
    assert expected_total_mb(budget, TEN | {"admin"}) == 3945
