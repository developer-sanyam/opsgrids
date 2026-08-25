"""Wiring for the three compliance-portal seams.

The service layer depends on an `IrpClient` (`app/irp/base.py`), an `ImsClient`
(`app/ims/base.py`) and an `EwayClient` (`app/eway/base.py`), never a concrete portal. This is the
single place the concrete ones are chosen — the deterministic stubs today, a NIC or GSP client
when credentials exist — so selecting a portal is a wiring change here, not a change to any rule.
All three are exposed as FastAPI dependencies so a test overrides them the same way it overrides a
session, which is how the rejection and outage paths are exercised without a portal that can be
persuaded to fail.

Three separate clients rather than one, because they are three separate products with separate
credentials and separate downtime: a business can be perfectly current on e-invoicing while its
e-Way integration is unreachable, and a single client would make that one outage.

Mirrors `services/payment/app/deps.py` deliberately: several integrations, one shape.
"""

from __future__ import annotations

from functools import lru_cache

from app.eway.base import EwayClient
from app.eway.stub import build_client as build_eway
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


@lru_cache(maxsize=1)
def _build_eway() -> EwayClient:
    # Same bargain, same absence of credentials. A NIC e-Way or GSP client would read its key
    # here — a *different* key from the IRP's, which is why this is its own client.
    return build_eway()


def get_eway_client() -> EwayClient:
    """The configured e-Way client. Cached per process; overridable in tests."""
    return _build_eway()
