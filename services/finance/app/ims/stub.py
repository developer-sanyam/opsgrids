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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import domain
from app.ims.base import ImsSupplierDocument

PORTAL = "stub-ims"


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

    def fetch(self, *, gstin: str, period: str) -> list[ImsSupplierDocument]:
        """The documents suppliers filed against `gstin` for `period`.

        The recipient's GSTIN is not decoration: its state code decides every fixture's tax heads,
        so the same fixture is CGST+SGST for a Maharashtra recipient and IGST for a Gujarat one —
        which is the behaviour a real feed has, and the behaviour a reconciler that only ever saw
        one shape would silently mis-handle.
        """
        recipient = domain.parse_gstin(gstin)
        year, month = domain.parse_period(period)
        return [
            self._document(fixture, seq, recipient.state_code, period, year, month)
            for seq, fixture in enumerate(FIXTURES, start=1)
        ]

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
        )


def build_client() -> StubImsClient:
    """Construct the configured IMS client. Only the stub exists today; a GSTN or GSP client would
    be selected here, and because it satisfies the same `Protocol`, nothing downstream changes."""
    return StubImsClient()
