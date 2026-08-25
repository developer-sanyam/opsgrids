"""Wiring for the two compliance-portal seams.

The service layer depends on an `IrpClient` (`app/irp/base.py`) and an `ImsClient`
(`app/ims/base.py`), never a concrete portal. This is the single place the concrete ones are
chosen — the deterministic stubs today, a NIC or GSP client when credentials exist — so selecting
a portal is a wiring change here, not a change to any rule. Both are exposed as FastAPI
dependencies so a test overrides them the same way it overrides a session, which is how the
rejection and outage paths are exercised without a portal that can be persuaded to fail.

Mirrors `services/payment/app/deps.py` deliberately: several integrations, one shape.
"""

from __future__ import annotations

from functools import lru_cache

from app.ims.base import ImsClient
from app.ims.stub import build_client as build_ims
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


@lru_cache(maxsize=1)
def _build_ims() -> ImsClient:
    # Same bargain, same absence of credentials. A GSTN or GSP client would read its key here.
    return build_ims()


def get_ims_client() -> ImsClient:
    """The configured IMS client. Cached per process; overridable in tests."""
    return _build_ims()
