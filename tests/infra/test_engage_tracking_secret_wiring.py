"""`ENGAGE_TRACKING_SECRET` reaches every notification process that mints or verifies a token.

The setting is read (`services/notification/app/e1_tracking.py`) and documented as a way to rotate
tracking links independently of unsubscribe links, but compose never passed it, so setting it on a
box did nothing. Passing it to only some notification processes is worse than none: one with the
key and one without sign and verify with different keys and every link answers 404. So the API and
the `split-workers` rollback worker share one environment anchor, and no other service carries it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infrastructure/edge/compose.prod.yml"
ENV_EXAMPLE = ROOT / "infrastructure/edge/.env.example"
KEY = "ENGAGE_TRACKING_SECRET"


def _services() -> dict[str, dict]:
    return yaml.safe_load(COMPOSE.read_text())["services"]


def test_notification_and_its_worker_both_receive_the_key_with_an_empty_default() -> None:
    services = _services()
    for name in ("notification", "notification-worker"):
        assert services[name]["environment"].get(KEY) == "${ENGAGE_TRACKING_SECRET:-}", name


def test_only_notification_processes_carry_it() -> None:
    carriers = sorted(
        name
        for name, cfg in _services().items()
        if isinstance(cfg.get("environment"), dict) and KEY in cfg["environment"]
    )
    assert carriers == ["notification", "notification-worker"]


def test_the_shared_service_env_block_does_not_carry_it() -> None:
    assert KEY not in yaml.safe_load(COMPOSE.read_text())["x-service-env"]


def test_the_example_keeps_it_optional_and_no_longer_says_it_is_not_forwarded() -> None:
    text = ENV_EXAMPLE.read_text()
    assert f"\n# {KEY}=\n" in text, "must stay commented out: an unset key is the supported default"
    assert "NOT YET FORWARDED" not in text
