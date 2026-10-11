"""Every CRM event an Engage recipe triggers on is one notification lets a rule subscribe to.

A recipe is a starting point offered on an empty automation list. Its trigger is a routing key, and
the builder, the publish check and the consumer all work from notification's explicit list
(`domain.SUBSCRIBABLE_EVENTS`): a recipe that names a key outside it opens with a trigger the
service refuses, and nothing in the frontend suite can see that, because the list lives in Python.
"Welcome a new lead" triggered on `crm.lead.created` while no lead key was subscribable, and shipped
that way.

The recipes are read as text and the list from a fresh interpreter in `services/notification`
(`import app` is ambiguous across services). Only `crm.*` triggers are held to the list: the other
products' recipes are those products' to keep, and one of them ("Follow up a storefront enquiry")
is known to wait for its key (registry `EV42`).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPES = (
    REPO_ROOT / "frontend" / "product-engage" / "src" / "routes" / "automations" / "recipes.ts"
)
NOTIFICATION = REPO_ROOT / "services" / "notification"

_TRIGGER = re.compile(r'emptyGraph\(\s*"([^"]+)"\s*\)')


def _subscribable_events() -> set[str]:
    script = (
        "import json\n"
        "from app.domain import SUBSCRIBABLE_EVENTS\n"
        "print(json.dumps(list(SUBSCRIBABLE_EVENTS)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=NOTIFICATION,
        check=True,
        capture_output=True,
        text=True,
    )
    return {str(key) for key in json.loads(result.stdout)}


def test_every_crm_recipe_trigger_is_subscribable() -> None:
    triggers = _TRIGGER.findall(RECIPES.read_text(encoding="utf-8"))
    assert triggers, "found no emptyGraph(...) trigger in recipes.ts: this test would be vacuous"
    crm = sorted({trigger for trigger in triggers if trigger.startswith("crm.")})
    assert crm, "expected at least one crm.* recipe trigger in recipes.ts"
    missing = sorted(set(crm) - _subscribable_events())
    assert not missing, (
        f"Engage recipes trigger on {missing}, which notification's SUBSCRIBABLE_EVENTS does not "
        "list: add the key in services/notification/app/domain.py (with its schema under "
        "libs/contracts/events), or change the recipe"
    )
