"""A deterministic e-Way bill portal — real everywhere except the wire.

The same bargain `StubIrpClient` and `StubImsClient` strike, for the same reason: there are no
NIC e-Way credentials in development, there is no sandbox a CI run may depend on, and the state
machine around the portal is where the bugs live.

What is **real** here:

* **The validations.** A consignment is refused for the reasons the portal refuses one: a bad
  GSTIN, a distance outside 1 to 4,000 km, a road movement with no vehicle number, a non-road
  movement with no transporter, a line with no HSN, a consignment at or below the ₹50,000
  threshold, lines that do not sum to the consignment. Each refusal is a numbered code, and the
  code is what reaches `eway_bills.error_code`.
* **Idempotency.** A generation carrying `EwayPayload.idempotency_key` (every one the service
  layer sends) is numbered from **that key**, and a client that has already issued a bill for it
  answers a repeat with `EwayAlreadyGenerated` carrying the first bill — the portal's own
  duplicate answer, so a resumed claim (D3) exercises the "already generated" path for real. A
  repeat withdrawal of a bill it has already withdrawn is `EwayAlreadyCancelled`. The memory is
  this client instance's (one per process, `app/deps.py`); across a restart the number is still
  the same, because it is a function of the key.
* **The bill number.** Twelve digits, as the portal issues them. With no idempotency key (a
  direct caller, a test double's payload) it is derived from the document it covers **and the
  moment the bill was raised**. Deterministic in its inputs — the same attempt
  reproduces the same number — but *not* one number per document, which is the difference from an
  IRN and it is a real one: an IRN is a hash of the document number and financial year and there
  is exactly one per document forever, while a document legitimately carries several e-Way bills
  over its life. It is cancelled and re-raised, or it expires in transit and the goods still have
  to arrive. Keying this on the document alone would refuse the second bill on the unique index —
  a lorry stuck at a yard because a hash said so.

  The real portal issues from a sequence rather than a hash, which is the one place this stub is
  *shaped* like the portal rather than identical to it, and it is called out here rather than
  left to be discovered.

What is **stubbed** — the one seam:

* **The outbound HTTP.** `generate` and `cancel` compute their answer instead of asking. No socket
  is opened and no credential is needed, which a test asserts.

**Validity is not here at all.** The portal returns a deadline; this client deliberately does not,
because the deadline is arithmetic over the distance the platform stored and the moment the bill
was raised (`domain.eway_valid_until`). A number only the portal can produce is a number no screen
can explain and no test can check, and at a roadside checkpoint the disagreement costs a driver.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from app import domain
from app.eway.base import (
    EwayAck,
    EwayAlreadyCancelled,
    EwayAlreadyGenerated,
    EwayCancelAck,
    EwayPayload,
    EwayRejected,
)

PORTAL = "stub-eway"

# Error codes, in the order a wrong consignment usually trips them. They mirror the shape of the
# NIC list — a stable numeric code plus a message — and use its numbering where this platform is
# confident of it. The contract the service layer depends on is that a refusal carries a code it
# can store and a message a human can act on.
ERR_INVALID_SUPPLIER_GSTIN = "102"
ERR_INVALID_RECIPIENT_GSTIN = "103"
ERR_INVALID_PLACE_OF_SUPPLY = "108"
ERR_BELOW_THRESHOLD = "111"
ERR_INVALID_DISTANCE = "112"
ERR_VEHICLE_REQUIRED = "113"
ERR_TRANSPORTER_REQUIRED = "114"
ERR_HSN_REQUIRED = "116"
ERR_LINES_DO_NOT_FOOT = "119"
ERR_NO_LINES = "120"
ERR_INVALID_TRANSPORT_MODE = "122"
ERR_INVALID_REASON = "126"


class StubEwayClient:
    """An `EwayClient` that answers from arithmetic. Network stubbed; everything else real."""

    name = PORTAL

    def __init__(self) -> None:
        #: Bills issued, by idempotency key — what lets a repeat be answered "already generated".
        self._issued: dict[str, EwayAck] = {}
        #: Bills withdrawn, by number — what lets a repeat be answered "already cancelled".
        self._cancelled: dict[str, EwayCancelAck] = {}

    # ------------------------------------------------------------------ generation

    def generate(self, payload: EwayPayload) -> EwayAck:
        """Validate the consignment the way the portal does, then issue its bill number — or,
        for a key this client already issued a bill under, answer "already generated"."""
        if payload.idempotency_key and payload.idempotency_key in self._issued:
            raise EwayAlreadyGenerated(self._issued[payload.idempotency_key])
        self._validate(payload)
        number = (
            compute_eway_bill_no_for_key(payload.idempotency_key)
            if payload.idempotency_key
            else compute_eway_bill_no(
                seller_gstin=payload.seller_gstin,
                document_type=payload.document_type,
                document_number=payload.document_number,
                raised_at=payload.generated_at.isoformat(),
            )
        )
        # The portal stamps its own clock; here the caller's is the clock, which is what makes an
        # attempt reproducible — and what makes the validity computed from it reproducible too.
        ack = EwayAck(eway_bill_no=number, valid_from=payload.generated_at)
        if payload.idempotency_key:
            self._issued[payload.idempotency_key] = ack
        return ack

    def cancel(
        self, *, eway_bill_no: str, reason_code: str, remark: str, at: datetime
    ) -> EwayCancelAck:
        """Withdraw a bill.

        The 24-hour window is **not** checked here, on purpose: this stub holds no record of when
        the bill was raised, and neither would a client for a portal that answers from its own
        state. The window is enforced against the platform's own ledger before the call is made,
        where it is testable without a portal at all.
        """
        if reason_code not in domain.EWAY_CANCELLATION_REASONS:
            raise EwayRejected(
                ERR_INVALID_REASON,
                f"{reason_code!r} is not a cancellation reason code the portal accepts",
            )
        if not eway_bill_no.isdigit() or len(eway_bill_no) != 12:
            raise EwayRejected(ERR_INVALID_REASON, "an e-Way bill number is 12 digits")
        if eway_bill_no in self._cancelled:
            raise EwayAlreadyCancelled(self._cancelled[eway_bill_no])
        ack = EwayCancelAck(eway_bill_no=eway_bill_no, cancel_date=at)
        self._cancelled[eway_bill_no] = ack
        return ack

    # ------------------------------------------------------------------ validation

    def _validate(self, payload: EwayPayload) -> None:
        """Every check the portal would run, in the order it would run them."""
        if not domain.is_valid_gstin(payload.seller_gstin):
            raise EwayRejected(
                ERR_INVALID_SUPPLIER_GSTIN, f"supplier GSTIN {payload.seller_gstin!r} is invalid"
            )
        if payload.buyer_gstin and not domain.is_valid_gstin(payload.buyer_gstin):
            raise EwayRejected(
                ERR_INVALID_RECIPIENT_GSTIN, f"recipient GSTIN {payload.buyer_gstin!r} is invalid"
            )
        if payload.place_of_supply not in domain.GST_STATE_CODES:
            raise EwayRejected(
                ERR_INVALID_PLACE_OF_SUPPLY,
                f"place of supply {payload.place_of_supply!r} is not a GST state code",
            )
        if payload.transport_mode not in domain.TRANSPORT_MODES:
            raise EwayRejected(
                ERR_INVALID_TRANSPORT_MODE,
                f"transport mode {payload.transport_mode!r} is not one the portal accepts",
            )

        # The threshold is the portal's own gate, not only the platform's: raising a bill for a
        # consignment that needs none is a refusal, not a courtesy.
        if abs(payload.consignment_value_minor) <= domain.EWAY_THRESHOLD_MINOR:
            raise EwayRejected(
                ERR_BELOW_THRESHOLD,
                "a consignment at or below 50,000 rupees needs no e-Way bill",
            )
        if not (domain.EWAY_MIN_DISTANCE_KM <= payload.distance_km <= domain.EWAY_MAX_DISTANCE_KM):
            raise EwayRejected(
                ERR_INVALID_DISTANCE,
                f"{payload.distance_km} km is outside the range the portal accepts "
                f"({domain.EWAY_MIN_DISTANCE_KM} to {domain.EWAY_MAX_DISTANCE_KM} km)",
            )

        # Who is carrying it, and how, is the branch that actually differs between modes.
        if payload.transport_mode == "road":
            if not payload.vehicle_no:
                raise EwayRejected(
                    ERR_VEHICLE_REQUIRED, "a road movement must name the vehicle carrying it"
                )
        elif not payload.transporter_id and not payload.transport_doc_no:
            raise EwayRejected(
                ERR_TRANSPORTER_REQUIRED,
                f"a {payload.transport_mode} movement must name a transporter or a transport "
                "document",
            )

        if not payload.lines:
            raise EwayRejected(ERR_NO_LINES, "a consignment must carry at least one line")
        for line in payload.lines:
            if not line.hsn_sac:
                raise EwayRejected(
                    ERR_HSN_REQUIRED, f"line {line.serial_no} carries no HSN/SAC code"
                )

        if sum(line.taxable_minor for line in payload.lines) != payload.taxable_minor:
            raise EwayRejected(
                ERR_LINES_DO_NOT_FOOT,
                "the lines' taxable values do not sum to the consignment's taxable value",
            )
        tax = payload.cgst_minor + payload.sgst_minor + payload.igst_minor
        if payload.taxable_minor + tax != payload.consignment_value_minor:
            raise EwayRejected(
                ERR_LINES_DO_NOT_FOOT,
                "taxable value plus tax does not equal the consignment value",
            )


# --------------------------------------------------------------------- the formulas


def compute_eway_bill_no(
    *, seller_gstin: str, document_type: str, document_number: str, raised_at: str
) -> str:
    """A 12-digit e-Way bill number, derived from the document **and the moment it was raised**.

    Deterministic in its inputs, so the same attempt always yields the same number and the whole
    state machine is reproducible without a network. It is deliberately *not* one number per
    document, the way an IRN is: a document carries several e-Way bills over its life — cancelled
    and re-raised, or expired in transit while the goods still have to arrive — and a number keyed
    on the document alone would collide on the unique index the second time, stranding a lorry
    because a hash said so.

    `raised_at` travels on the payload rather than being read from a clock in here, which is what
    keeps that determinism testable.

    The real portal issues from a sequence, so this is the one place the stub is *shaped* like the
    portal rather than identical to it. A real client returns whatever NIC issued and nothing
    downstream changes: the number is stored, never recomputed.
    """
    material = f"{seller_gstin}{document_type}{document_number}{raised_at}"
    digest = hashlib.sha256(material.encode()).hexdigest()
    # Forced into the 100000000000 to 999999999999 band so it is always twelve digits, never eleven
    # with a leading zero that some downstream string comparison would quietly drop.
    return f"{int(digest[:16], 16) % 900_000_000_000 + 100_000_000_000:012d}"


def compute_eway_bill_no_for_key(idempotency_key: str) -> str:
    """A 12-digit bill number derived from a generation's idempotency key alone — so a resumed
    attempt (the same key, a later clock) is answered with the same number, as the real portal
    answers a duplicate with the bill it already issued. Same twelve-digit band as
    `compute_eway_bill_no`."""
    digest = hashlib.sha256(f"eway-key:{idempotency_key}".encode()).hexdigest()
    return f"{int(digest[:16], 16) % 900_000_000_000 + 100_000_000_000:012d}"


def build_client() -> StubEwayClient:
    """Construct the configured e-Way client. Only the stub exists today; a NIC or GSP client
    would be selected here, and because it satisfies the same `Protocol`, nothing changes."""
    return StubEwayClient()
