"""`DOCUMENT_ORPHAN_SWEEP_ENABLED` / `_DRY_RUN` are not required keys.

Since the memory-headroom merge no container reads them (compose does not pass them; the orphan
pass runs only by hand), yet they were uncommented in `.env.example`, and preflight derives its
required-key list from the uncommented assignments there, so every box was made to carry two lines
that changed nothing. They are commented out, which is the precedent for optional keys
(`ENGAGE_TRACKING_SECRET`, the payment switches); the document service's own defaults (off, dry
run) are what applies when they are absent.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
EDGE = ROOT / "infrastructure/edge"
KEYS = ("DOCUMENT_ORPHAN_SWEEP_ENABLED", "DOCUMENT_ORPHAN_SWEEP_DRY_RUN")


def _required_keys() -> set[str]:
    """Preflight's own derivation: `sed -n 's/^\\([A-Z][A-Z0-9_]*\\)=.*/\\1/p' .env.example`."""
    text = (EDGE / ".env.example").read_text()
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", text, flags=re.MULTILINE))


def test_neither_key_is_a_required_assignment() -> None:
    assert not set(KEYS) & _required_keys()


def test_both_stay_documented_as_commented_out_examples() -> None:
    text = (EDGE / ".env.example").read_text()
    for key in KEYS:
        assert f"\n# {key}=" in text, key


def test_the_document_service_defaults_are_the_safe_ones() -> None:
    config = (ROOT / "services/document/app/config.py").read_text()
    assert re.search(r"^\s+orphan_sweep_enabled: bool = False$", config, re.MULTILINE)
    assert re.search(r"^\s+orphan_sweep_dry_run: bool = True$", config, re.MULTILINE)


def test_compose_does_not_pass_them_so_documentation_matches_behaviour() -> None:
    services = yaml.safe_load((EDGE / "compose.prod.yml").read_text())["services"]
    for name, cfg in services.items():
        env = cfg.get("environment")
        if isinstance(env, dict):
            assert not set(KEYS) & set(env), name
