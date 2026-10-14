"""No setting that nothing reads.

`SENTRY_DSN` was declared in `platform_core.config`, passed to every service by compose, required
in `.env.example` and demanded by preflight, while no Sentry SDK exists in the lockfile and no code
read it. An operator who filled it in believed errors were being reported. The same went for
`otel_exporter_otlp_endpoint`. Both are removed; this test keeps them from drifting back in without
an SDK that uses them.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
CONFIG = REPO_ROOT / "libs/platform-core/src/platform_core/config.py"


def _assignment_keys(path: Path) -> set[str]:
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", path.read_text(), flags=re.MULTILINE))


def test_the_settings_class_declares_neither_dead_setting() -> None:
    text = CONFIG.read_text()
    assert not re.search(r"^\s+sentry_dsn\b", text, flags=re.MULTILINE)
    assert not re.search(r"^\s+otel_exporter_otlp_endpoint\b", text, flags=re.MULTILINE)


def test_compose_does_not_pass_a_dsn_to_every_service() -> None:
    assert "SENTRY_DSN" not in (EDGE_DIR / "compose.prod.yml").read_text()


def test_the_env_example_does_not_ask_for_a_dsn() -> None:
    keys = _assignment_keys(EDGE_DIR / ".env.example")
    assert "SENTRY_DSN" not in keys
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in keys


def test_preflight_does_not_mention_a_dsn() -> None:
    assert "SENTRY_DSN" not in (EDGE_DIR / "preflight.sh").read_text()


def test_the_operations_doc_says_error_tracking_is_not_wired() -> None:
    text = (REPO_ROOT / "docs/operations.md").read_text()
    assert "## Error tracking" in text
    assert "not wired" in text
