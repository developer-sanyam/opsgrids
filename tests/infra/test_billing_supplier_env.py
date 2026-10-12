"""The GST supplier settings (ADR-0004 Q1), as the box receives them.

Compose, `.env.example` and billing's `Settings` must name the same four keys, and none may be
required: they are inert until the owner supplies the facts, so an unconfigured box must deploy.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
KEYS = (
    "BILLING_SUPPLIER_GSTIN",
    "BILLING_SUPPLIER_LEGAL_NAME",
    "BILLING_SUPPLIER_STATE_CODE",
    "BILLING_DEFAULT_SAC",
)


def _billing_env() -> dict[str, object]:
    compose = yaml.safe_load((EDGE_DIR / "compose.prod.yml").read_text())
    return compose["services"]["billing"]["environment"]


@pytest.mark.parametrize("key", KEYS)
def test_compose_passes_each_key_to_billing_empty_by_default(key: str) -> None:
    assert _billing_env()[key] == f"${{{key}:-}}"


@pytest.mark.parametrize("key", KEYS)
def test_billing_settings_declare_each_key(key: str) -> None:
    config = (REPO_ROOT / "services/billing/app/config.py").read_text()
    assert re.search(rf"^    {key.lower()}: str \| None = None$", config, re.MULTILINE), key


@pytest.mark.parametrize("key", KEYS)
def test_env_example_documents_each_key_commented_with_no_value(key: str) -> None:
    lines = (EDGE_DIR / ".env.example").read_text().splitlines()
    assert f"# {key}=" in lines, f"{key} is not documented commented-out"
    assert not any(line.startswith(f"{key}=") for line in lines), f"{key} became required"
