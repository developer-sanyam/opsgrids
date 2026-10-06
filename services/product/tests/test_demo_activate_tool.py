"""`tools/demo/activate_product.py` — the demo workspace's activation script — and the gate.

The script writes the activation row on the admin engine, bypassing the API, so it cannot refuse
for the API's reasons. What it owes the operator is to say so: an organization with no subscription
(the case it exists for) activates in silence; one a subscription has fed gets a warning on stderr
and `subscription_managed: true` in its JSON line.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.config import settings
from app.consumers import project_subscription_event
from platform_core.db import dispose_engine, init_engine
from tests.helpers import real_envelope

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
SERVICE = REPO / "services" / "product"
TOOL = REPO / "tools" / "demo" / "activate_product.py"


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    init_engine(settings())
    yield
    await dispose_engine()


def _run(org_id: uuid.UUID, product_key: str = "hrms") -> tuple[dict[str, Any], str]:
    result = subprocess.run(  # noqa: S603  (fixed argv, sys.executable, no shell)
        [sys.executable, str(TOOL), str(SERVICE), str(org_id), product_key],
        cwd=str(SERVICE),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1]), result.stderr


async def test_an_organization_with_no_subscription_activates_without_a_warning(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    out, stderr = _run(org_id)
    assert out["entitles"] is True and out["subscription_managed"] is False
    assert "managed by a subscription" not in stderr
    async with session.begin():
        activation = await selectors.get_activation(session, org_id, "hrms")
    assert activation is not None and activation.entitles


async def test_an_organization_a_subscription_has_fed_is_activated_with_a_clear_warning(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    await project_subscription_event(real_envelope(org_id, state="cancelled"))

    out, stderr = _run(org_id)

    assert out["subscription_managed"] is True
    assert "WARNING" in stderr and "managed by a subscription" in stderr
    assert str(org_id) in stderr and "hrms" in stderr


async def test_the_warning_is_per_product(session: AsyncSession, org_id: uuid.UUID) -> None:
    await project_subscription_event(real_envelope(org_id, state="cancelled", product_key="crm"))

    out, stderr = _run(org_id, "hrms")

    assert out["subscription_managed"] is False
    assert "managed by a subscription" not in stderr
