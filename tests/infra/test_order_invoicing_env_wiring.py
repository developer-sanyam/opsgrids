"""`ORDER_INVOICING_MODE` as the box receives it (INVENTORY-01): compose, `.env.example`, the
source matrix and operations' own setting agree, and the safe default is `log_only` everywhere.

Same pattern as `test_payment_env_wiring.py`: compose is rendered with `yaml.safe_load`, so a key
that leaks into the shared `x-service-env` block fails here."""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
KEY = "ORDER_INVOICING_MODE"
DEFAULT = "log_only"


def _compose() -> dict:
    return yaml.safe_load((EDGE_DIR / "compose.prod.yml").read_text())


def test_only_operations_receives_it_with_the_safe_default() -> None:
    services = _compose()["services"]
    carriers = {
        name: cfg["environment"][KEY]
        for name, cfg in services.items()
        if isinstance(cfg.get("environment"), dict) and KEY in cfg["environment"]
    }
    assert carriers == {"operations": f"${{{KEY}:-{DEFAULT}}}"}
    assert KEY not in _compose()["x-service-env"]


def test_operations_reaches_finance_and_finance_reaches_customer() -> None:
    services = _compose()["services"]
    assert services["operations"]["environment"]["FINANCE_INTERNAL_URL"] == "http://finance:8000"
    assert services["finance"]["environment"]["CUSTOMER_INTERNAL_URL"] == "http://customer:8000"


def test_env_example_documents_it_commented_with_the_same_default() -> None:
    text = (EDGE_DIR / ".env.example").read_text()
    lines = text.splitlines()
    assert f"# {KEY}={DEFAULT}" in lines
    assert not any(line.startswith(f"{KEY}=") for line in lines), "must not become required"
    block = text.split(f"# >> {KEY}", 1)[1].split("# >> ", 1)[0]
    assert "consumer: operations" in block


def test_the_service_default_matches() -> None:
    source = (REPO_ROOT / "services/operations/app/config.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == KEY.lower()
        ):
            assert isinstance(node.value, ast.Constant) and node.value.value == DEFAULT
            return
    raise AssertionError(f"{KEY.lower()} not declared in operations' Settings")


def test_the_source_matrix_has_a_row() -> None:
    matrix = (REPO_ROOT / "docs/deploy/ENV_SOURCE_MATRIX.md").read_text().splitlines()
    assert any(line.startswith(f"| `{KEY}`") for line in matrix)
