"""A deterministic Invoice Management System — real everywhere except the wire.

The same bargain `app/irp/stub.py` strikes, and for the same reason: there are no GSTN or GSP
credentials in development, there is no sandbox a CI run may depend on, and the *reconciliation*
is where the bugs live. So the portal is arithmetic and the queue behind it is real.

What is **real** here:

* **The tax.** Every fixture's CGST/SGST/IGST split is computed by `domain.split_gst` against the
  recipient's own state, so an inbound document from a supplier in the recipient's state is taxed
  CGST+SGST and one from anywhere else is taxed IGST — and the figures foot exactly, because they
  come from the one function on this platform that is allowed to compute a tax split. A fixture
  with hand-typed heads would be a second implementation of that calculation, and the reconciler
  would eventually be right about a document the fixtures were wrong about.
* **The identities.** Every supplier GSTIN below is checksum-valid, so a document that reaches the
  reconciler carries a number `domain.parse_gstin` accepts, exactly as a real one would.
* **The shape of the queue.** The five documents are chosen so that a purchase book containing
  the obvious counterparts produces every reason class the reconciler can give — an exact match, a
  money difference, a supplier nothing was bought from, an unrecorded number, and the same
  document arriving twice. A stub that only ever produced clean matches would leave four fifths of
  the product untested by eye.

What is **stubbed** — the one seam:

* **The outbound HTTP.** `fetch` computes its answer instead of asking. No socket is opened and no
  credential is needed, which a test asserts.

Fixtures are **period-relative**: the dates and the portal references are derived from the period
asked for, so any period returns the same five documents under references unique to it. That keeps
a re-sync of one period idempotent and a sync of two periods additive, which is the property the
ingest is written against.

**Pushing decisions back (C2)** is real here too, everywhere except the wire. The stub remembers,
per (period, portal row), the action it holds and the decision version it took it under, and it
answers the way the portal is contracted to (`app/ims/base.py`): the same (period, row, version)
again is `already_recorded` — success by fetch — a different action under the same version is a
`conflict`, an older version than the one held is a `conflict`, a row the period does not have is
a per-document `rejected`, and a request the portal will not take at all (a malformed period, a
batch over its limit, an unknown GSTIN) is an `ImsPushRejected`. What it holds is what the next
`fetch` reports as each document's `action`, so the whole push-then-sync-then-check-drift cycle
runs against the stub for real. `simulate_external_action` stands in for somebody acting on the
portal directly — the one thing a test cannot otherwise reach.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import domain
from app.ims.base import ImsPushItem, ImsPushRejected, ImsPushResult, ImsSupplierDocument

PORTAL = "stub-ims"

# The stub's own numbered refusals, in the same spirit as `app/eway/stub.py`: a stable code and a
# message a person can act on. The contract the service depends on is that a refusal carries a
# code it can store and words it can show.
ERR_INVALID_PERIOD = "IMS101"
ERR_BATCH_TOO_LARGE = "IMS102"
ERR_INVALID_GSTIN = "IMS103"
ERR_UNKNOWN_DOCUMENT = "IMS201"
ERR_INVALID_ACTION = "IMS202"
ERR_STALE_VERSION = "IMS301"
ERR_ACTION_MISMATCH = "IMS302"


@dataclass(frozen=True, slots=True)
class _Fixture:
    """One supplier document, before its period, its dates and its tax are resolved.

    A rate rather than tax heads: the heads are computed against the recipient's state when the
    document is built, so a fixture cannot carry an arithmetic mistake of its own.
    """

    gstin: str
    name: str
    document_number: str
    day: int
    taxable: int
    #: A decimal string, so the rate reaches `split_gst` exactly rather than through a float.
    rate: str


#: The queue, in the order the portal lists it. Amounts are paise.
#:
#: Read as a story: a textile supplier bills what was ordered, a foods supplier bills a little more
#: than was ordered, a supplier nobody has heard of bills at all, the textile supplier's document
#: is re-filed by mistake, and a logistics supplier bills for something not yet recorded. Four of
#: the five need a human; exactly one does not.
FIXTURES: tuple[_Fixture, ...] = (
    _Fixture(
        gstin="24AABCM1234F1Z3",
        name="Meridian Textiles Pvt Ltd",
        document_number="MT-4471",
        day=4,
        taxable=4_50_000,
        rate="18",
    ),
    _Fixture(
        gstin="33AAACK5678G1ZH",
        name="Kanaka Foods Ltd",
        document_number="KF-2210",
        day=9,
        taxable=2_80_000,
        rate="12",
    ),
    _Fixture(
        gstin="29AADCS3456J1Z0",
        name="Sahyadri Components",
        document_number="ZR-0088",
        day=12,
        taxable=1_20_000,
        rate="18",
    ),
    # The first document again, re-filed by the supplier under a second portal reference. Identical
    # in every field that identifies it, which is exactly why the portal reference and not the
    # document number is what `ims_documents` is unique on.
    _Fixture(
        gstin="24AABCM1234F1Z3",
        name="Meridian Textiles Pvt Ltd",
        document_number="MT-4471",
        day=4,
        taxable=4_50_000,
        rate="18",
    ),
    _Fixture(
        gstin="27AAECO9012H1ZG",
        name="Orbit Logistics",
        document_number="OL-9002",
        day=21,
        taxable=90_000,
        rate="5",
    ),
)


class StubImsClient:
    """An `ImsClient` that answers from arithmetic. Network stubbed; everything else real."""

    name = PORTAL
    #: Small enough that a test can prove the caller splits a larger set, large enough that no
    #: ordinary period needs more than a call or two.
    push_batch_limit = domain.IMS_PUSH_BATCH_MAX

    def __init__(self) -> None:
        #: What the portal holds for each row, and the version it took it under — keyed by
        #: (period, portal row reference). The memory is this instance's (one per process,
        #: `app/deps.py`).
        self._held: dict[tuple[str, str], tuple[str, int]] = {}
        #: Every push call, as `(period, [idempotency keys])` — what "asked once" is asserted on.
        self.push_calls: list[tuple[str, list[str]]] = []

    def fetch(self, *, gstin: str, period: str) -> list[ImsSupplierDocument]:
        """The documents suppliers filed against `gstin` for `period`.

        The recipient's GSTIN is not decoration: its state code decides every fixture's tax heads,
        so the same fixture is CGST+SGST for a Maharashtra recipient and IGST for a Gujarat one —
        which is the behaviour a real feed has, and the behaviour a reconciler that only ever saw
        one shape would silently mis-handle.

        Each document carries the action the portal currently holds for it (`no_action` until a
        decision is pushed), so a sync after a push can be checked against what was pushed.
        """
        recipient = domain.parse_gstin(gstin)
        year, month = domain.parse_period(period)
        return [
            self._document(fixture, seq, recipient.state_code, period, year, month)
            for seq, fixture in enumerate(FIXTURES, start=1)
        ]

    # ------------------------------------------------------------------ pushing decisions

    def push_ims_decisions(
        self, *, gstin: str, period: str, decisions: Sequence[ImsPushItem]
    ) -> list[ImsPushResult]:
        """Record decisions the way the portal does: whole-request validation first, then a verdict
        per document."""
        self.push_calls.append((period, [item.idempotency_key for item in decisions]))
        try:
            domain.parse_period(period)
        except ValueError as exc:
            raise ImsPushRejected(ERR_INVALID_PERIOD, str(exc)) from exc
        if not domain.is_valid_gstin(gstin):
            raise ImsPushRejected(ERR_INVALID_GSTIN, f"GSTIN {gstin!r} is not one the portal knows")
        if len(decisions) > self.push_batch_limit:
            raise ImsPushRejected(
                ERR_BATCH_TOO_LARGE,
                f"{len(decisions)} decisions exceed the {self.push_batch_limit} the portal takes "
                "in one request",
            )
        return [self._record(period, item) for item in decisions]

    def _record(self, period: str, item: ImsPushItem) -> ImsPushResult:
        known = {f"{period}/{seq:04d}" for seq in range(1, len(FIXTURES) + 1)}
        if item.source_ref not in known:
            return ImsPushResult(
                item.source_ref,
                "rejected",
                code=ERR_UNKNOWN_DOCUMENT,
                message=f"the portal has no document {item.source_ref!r} for {period}",
            )
        if item.action not in domain.IMS_PORTAL_ACTIONS.values():
            return ImsPushResult(
                item.source_ref,
                "rejected",
                code=ERR_INVALID_ACTION,
                message=f"{item.action!r} is not an action the portal accepts",
            )
        held = self._held.get((period, item.source_ref))
        if held is not None:
            held_action, held_version = held
            if held_version == item.version:
                if held_action == item.action:
                    # The same decision under the same key: the answer a repeat is owed.
                    return ImsPushResult(
                        item.source_ref, "already_recorded", recorded_action=held_action
                    )
                return ImsPushResult(
                    item.source_ref,
                    "conflict",
                    code=ERR_ACTION_MISMATCH,
                    message=f"the portal holds {held_action!r} for this document, not "
                    f"{item.action!r}",
                    recorded_action=held_action,
                )
            if held_version > item.version:
                return ImsPushResult(
                    item.source_ref,
                    "conflict",
                    code=ERR_STALE_VERSION,
                    message="a newer decision is already recorded for this document",
                    recorded_action=held_action,
                )
        self._held[(period, item.source_ref)] = (item.action, item.version)
        return ImsPushResult(item.source_ref, "recorded", recorded_action=item.action)

    def simulate_external_action(self, period: str, source_ref: str, action: str) -> None:
        """Somebody acted on the row on the portal directly, outside this platform — what makes
        the next fetch disagree with what was pushed. The version is bumped past anything this
        platform could have sent."""
        held = self._held.get((period, source_ref))
        self._held[(period, source_ref)] = (action, (held[1] if held else 0) + 1_000)

    def _document(
        self,
        fixture: _Fixture,
        seq: int,
        recipient_state: str,
        period: str,
        year: int,
        month: int,
    ) -> ImsSupplierDocument:
        supplier_state = fixture.gstin[:2]
        # A purchase is taxed where the recipient is: intra-state when the supplier sits in the
        # same state, inter-state otherwise. `is_intra_state` is asked rather than a `==` written
        # here, so an export or SEZ rule added there reaches this feed without an edit.
        intra = domain.is_intra_state(
            supply_type="b2b",
            seller_state_code=supplier_state,
            place_of_supply=recipient_state,
        )
        split = domain.split_gst(fixture.taxable, Decimal(fixture.rate), intra_state=intra)
        return ImsSupplierDocument(
            source_ref=f"{period}/{seq:04d}",
            supplier_gstin=fixture.gstin,
            supplier_name=fixture.name,
            document_number=fixture.document_number,
            document_type="invoice",
            document_date=date(year, month, fixture.day),
            period=period,
            taxable_minor=fixture.taxable,
            cgst_minor=split.cgst_minor,
            sgst_minor=split.sgst_minor,
            igst_minor=split.igst_minor,
            total_minor=fixture.taxable + split.total_minor,
            # No IRN on any fixture, and that is the harder case rather than a shortcut: a
            # supplier's own reference would make the duplicate obvious at a glance, so leaving it
            # out forces the reconciler to recognise the document from its own identity — whose it
            # is, what kind, which number, which financial year — which is what it has to do for
            # every supplier below the e-invoicing threshold anyway.
            irn=None,
            action=self._held.get((period, f"{period}/{seq:04d}"), ("no_action", 0))[0],
        )


def build_client() -> StubImsClient:
    """Construct the configured IMS client. Only the stub exists today; a GSTN or GSP client would
    be selected here, and because it satisfies the same `Protocol`, nothing downstream changes."""
    return StubImsClient()
