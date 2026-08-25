"""Wiring for the IRP seam.

The service layer depends on an `IrpClient` (`app/irp/base.py`), never a concrete portal. This is
the single place the concrete one is chosen — the deterministic stub today, a NIC or GSP client
when credentials exist — so selecting a portal is a wiring change here, not a change to any rule.
Exposed as a FastAPI dependency so a test overrides it the same way it overrides a session, which
is how the rejection paths are exercised without a portal that can be persuaded to reject.

Mirrors `services/payment/app/deps.py` deliberately: two integrations, one shape.
"""

from __future__ import annotations

from functools import lru_cache

from app.irp.base import IrpClient
from app.irp.stub import build_client


@lru_cache(maxsize=1)
def _build() -> IrpClient:
    # No credentials are read: the stub needs none, and a test asserts that filing works with
    # none configured. A real client would take its GSP key and secret from `settings()` here.
    return build_client()


def get_irp_client() -> IrpClient:
    """The configured IRP client. Cached per process; overridable in tests."""
    return _build()
