"""Runs `DirectDebitContract` against the reference adapter — and against seven broken ones — and
pins that the service already routes a `direct`-shaped provider down the no-Order path.

No live adapter has this shape in Phase A (the Cashfree adapter *declares* it and builds none of
it): the reference adapter is what the contract is proven against today, and what the first live one
is measured against tomorrow.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from app import services
from app.models import Charge
from app.providers.base import ChargeResult, WebhookEvent, capabilities_of
from app.providers.cashfree import CashfreeProvider
from app.providers.cashfree_fake import CashfreeFakeWire
from tests.direct_debit_contract import (
    CountingDebitAdapter,
    DirectDebitContract,
    ReferenceDirectDebit,
    new_debit_charge,
)


class _Contract(DirectDebitContract):
    def fresh(self, adapter: Any) -> Any:
        return type(adapter)(adapter.debits)

    def webhook_bodies(self, adapter: Any) -> tuple[bytes, bytes]:
        return (
            json.dumps({"event": "debit.success", "id": "d1", "at": 100}).encode(),
            json.dumps({"event": "debit.failed", "id": "d2", "at": 200}).encode(),
        )


class TestTheReferenceAdapter(_Contract):
    @pytest.fixture
    def adapter(self) -> ReferenceDirectDebit:
        return ReferenceDirectDebit()


# --------------------------------------------------------------------------- broken adapters


class _DebitPerCall(ReferenceDirectDebit):
    """No lookup: `find_debit` never finds, so the service debits on every call — a double debit."""

    def find_debit(self, *, charge_ref: str) -> ChargeResult | None:
        return None


class _FindThatCreates(ReferenceDirectDebit):
    """`find_debit` debits on a miss: every look leaves a debit behind."""

    def find_debit(self, *, charge_ref: str) -> ChargeResult | None:
        found = super().find_debit(charge_ref=charge_ref)
        if found is not None:
            return found
        return self.debit(
            idempotency_key="k",
            charge_ref=charge_ref,
            mandate_id=None,
            amount_minor=1,
            currency="INR",
            notes={},
        )


class _ForgetsAcrossProcesses(ReferenceDirectDebit):
    """Keeps its debits only in this process's memory: a fresh process finds nothing and debits."""

    def __init__(self, backing: dict[str, ChargeResult] | None = None) -> None:
        super().__init__({})


class _SlowWire(ReferenceDirectDebit):
    request_timeouts_seconds = (30.0, 30.0)


class _AcceptsAnySignature(ReferenceDirectDebit):
    def verify_webhook(self, *, signature: str | None, body: bytes) -> bool:
        return bool(signature)


class _CrashesOnNonAscii(ReferenceDirectDebit):
    def verify_webhook(self, *, signature: str | None, body: bytes) -> bool:
        import hmac

        if not signature:
            return False
        return hmac.compare_digest(self.sign(body), signature)


class _ConstantEventId(ReferenceDirectDebit):
    def parse_webhook(self, *, body: bytes) -> WebhookEvent:
        return replace(super().parse_webhook(body=body), provider_event_id="evt_constant")


@pytest.mark.parametrize(
    ("adapter_cls", "check"),
    [
        (_DebitPerCall, "test_a_debit_is_created_at_most_once_per_charge"),
        (_FindThatCreates, "test_find_debit_never_creates"),
        (
            _FindThatCreates,
            "test_the_lookup_only_call_finds_what_a_create_made_and_never_makes_anything",
        ),
        (_ForgetsAcrossProcesses, "test_a_debit_is_created_at_most_once_per_charge"),
        (_SlowWire, "test_the_declared_request_timeouts_fit_inside_the_call_budget"),
        (_AcceptsAnySignature, "test_a_forged_signature_never_verifies_and_never_crashes"),
        (_CrashesOnNonAscii, "test_a_forged_signature_never_verifies_and_never_crashes"),
        (_ConstantEventId, "test_parse_webhook_never_invents_a_constant_event_id"),
    ],
    ids=lambda v: v if isinstance(v, str) else v.__name__,
)
def test_the_contract_fails_for_an_adapter_broken_the_way_it_guards_against(
    adapter_cls: type[ReferenceDirectDebit], check: str
) -> None:
    contract = _Contract()
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        getattr(contract, check)(adapter_cls())


# --------------------------------------------------------------------------- the service's branch


def test_the_service_routes_a_direct_provider_down_the_no_order_path() -> None:
    """`call_provider_charge` finds a debit and creates one only if none exists — and never asks a
    direct provider for an Order it does not have."""
    adapter = ReferenceDirectDebit()
    watched = CountingDebitAdapter(adapter)
    charge = new_debit_charge(adapter)
    result = services.call_provider_charge(watched.as_provider(), charge)
    assert result.provider == "reference"
    assert dict(watched.calls) == {"find_debit": 1, "debit": 1}
    assert "ensure_order" not in watched.calls and "find_order" not in watched.calls


def test_the_legacy_shape_is_still_order_then_charge() -> None:
    from app.providers.razorpay import RazorpayProvider

    stub = RazorpayProvider(key_id=None, key_secret=None, webhook_secret="x")
    assert capabilities_of(stub).debit_shape == "order_then_charge"
    assert capabilities_of(object()).debit_shape == "order_then_charge"


def test_cashfree_declares_the_direct_shape_and_builds_none_of_it() -> None:
    """Phase A declares; Phase B implements. The adapter says so out loud, never pretends."""
    cashfree = CashfreeProvider(wire=CashfreeFakeWire(), secret_key=None, mode="stub")
    assert cashfree.debit_shape == "direct"
    capabilities = capabilities_of(cashfree)
    assert capabilities.debit_shape == "direct"
    assert capabilities.requires_mandate_for_debit is True
    assert capabilities.supports_mandate_debit is False
    charge: Charge = new_debit_charge(cashfree)
    with pytest.raises(NotImplementedError):
        services.call_provider_charge(cashfree, charge)
    with pytest.raises(NotImplementedError):
        cashfree.find_debit(charge_ref="x")
