"""Every `*_internal_url` notification reads is set in its production environment.

An unset one falls back to its `localhost` default inside the container, so the call it makes
(conversation assignment's membership check, for `account`) fails on the box and nowhere else.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_every_internal_url_notification_reads_is_set_in_production() -> None:
    config = (ROOT / "services/notification/app/config.py").read_text()
    needed = {m.upper() for m in re.findall(r"^\s+(\w+_internal_url)\s*:", config, re.M)}
    compose = yaml.safe_load((ROOT / "infrastructure/edge/compose.prod.yml").read_text())
    env = compose["services"]["notification"]["environment"]
    assert needed, "expected notification to read at least one *_internal_url"
    assert not needed - set(env), f"unset in compose.prod.yml: {sorted(needed - set(env))}"
