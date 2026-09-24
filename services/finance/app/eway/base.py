"""The e-Way bill seam — one `Protocol` every e-Way portal client satisfies.

The third portal, and the one that is least like the other two. The IRP registers a *document*
and the IMS reads what suppliers *filed*; the e-Way bill portal licenses a **physical movement**,
which is why it is the only one of the three whose answer expires and the only one that wants a
vehicle number. Drawing it as its own client rather than a second method on `IrpClient` is that
difference made structural: the NIC e-Way API is a separate product with separate credentials and
separate downtime, and a business can be perfectly current on e-invoicing while its e-Way
integration is down.

What is deliberately encoded here rather than left to each implementation:

* **The platform owns validity, not the portal.** The ack carries a bill number and the moment it
  was generated; the deadline is computed by `domain.eway_valid_until` from the **stored**
  distance and that timestamp. A deadline that only the portal knows is a deadline a screen
  cannot show and a test cannot check, and the roadside consequence of the two disagreeing falls
  on a driver.
* **Refusal is data.** `EwayRejected` carries the portal's own numbered error, the same bargain
  `IrpRejected` strikes, and the code is what reaches `eway_bills.error_code`.
* **The clock is an input.** `generated_at` travels on the payload, so an attempt is a pure
  function of what was sent and the whole state machine is testable without a network.
* **Amounts are integer minor units all the way to the wire.** Converting to the rupees the
  government schema is written in is the adapter's job, at the last moment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable


class EwayRejected(Exception):
    """The portal refused the consignment. Carries the portal's own numbered error.

    Not a transport failure: the request arrived, was understood, and was rejected on its
    contents. The caller records a `failed` row and does not retry the identical consignment.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class EwayUnavailable(Exception):
    """The portal could not be reached or did not answer in time.

    Opposite to a rejection in what it licenses the caller to do: an outage is retryable with the
    identical consignment, and on this obligation it is urgent — the goods are already loaded.
    """


@dataclass(frozen=True, slots=True)
class EwayLine:
    """One line of the consignment as the portal validates it.

    HSN is not optional here the way it is on an invoice draft: the e-Way portal refuses a
    consignment whose goods it cannot classify, because the classification is what a checkpoint
    reads.
    """

    serial_no: int
    description: str
    hsn_sac: str | None
    quantity: Decimal
    taxable_minor: int
    rate_pct: Decimal
    cgst_minor: int
    sgst_minor: int
    igst_minor: int


@dataclass(frozen=True, slots=True)
class EwayPayload:
    """One goods movement, in the shape the portal reasons about.

    Not the government JSON — the neutral consignment, which each adapter maps onto whichever
    envelope its portal wants, so a GSP can be swapped for NIC without the service layer learning
    a second vocabulary.
    """

    #: INV | CRN — the same document-type codes the IRP uses.
    document_type: str
    document_number: str
    document_date: date

    seller_gstin: str
    seller_state_code: str
    #: Null for an export, where the recipient has no GSTIN. `URP` is what the portal prints.
    buyer_gstin: str | None
    buyer_name: str
    place_of_supply: str

    #: road | rail | air | ship.
    transport_mode: str
    #: Approximate road distance in kilometres. The **input** to validity.
    distance_km: int
    vehicle_no: str | None
    transporter_id: str | None
    transport_doc_no: str | None
    transport_doc_date: date | None
    over_dimensional: bool

    #: The consignment value the ₹50,000 threshold is tested against.
    consignment_value_minor: int
    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int

    #: The moment the caller made this attempt. An input, not a `now()` read inside an adapter.
    generated_at: datetime

    lines: list[EwayLine] = field(default_factory=list)

    #: The portal's own idempotency key for this generation: **(document, transporter/vehicle
    #: part)**. Two calls carrying the same key are one bill — a real portal answers the second
    #: with "already generated" and the number (`EwayAlreadyGenerated`). Empty for a caller that
    #: predates the key (a test double); the service layer always sets it.
    idempotency_key: str = ""


@dataclass(frozen=True, slots=True)
class EwayAck:
    """What the portal returns when it raises a bill.

    Two fields, and the absence of a third is the design: there is no `valid_until` here. The
    portal does return one, and taking it would make the deadline a number this platform holds
    without being able to explain — so validity is computed from `valid_from` and the stored
    distance by `domain.eway_valid_until`, and a test asserts the two agree.
    """

    eway_bill_no: str
    valid_from: datetime


@dataclass(frozen=True, slots=True)
class EwayCancelAck:
    """What the portal returns when a bill is withdrawn.

    Cancellation is legal within 24 hours of generation and only in whole. Past that the movement
    has to be reported as it happened, which is why the window is enforced against the platform's
    own ledger before the call is made.
    """

    eway_bill_no: str
    cancel_date: datetime


class EwayAlreadyGenerated(Exception):
    """The portal already holds a bill for this (document, transporter/vehicle part) — success by
    lookup, wearing a refusal's shape.

    Generation is idempotent by the portal's own key, so a repeat — a retry that raced a first
    success, or a resumed claim after a crash between `call_eway_generate` and
    `record_eway_generation` (D3) — is answered with the bill it already issued rather than a
    second one. A real adapter maps that answer onto this, carrying the ack, so the resumed attempt
    is recorded as the `generated` it is and never as a `failed`.
    """

    def __init__(self, ack: EwayAck) -> None:
        super().__init__(f"already generated: e-Way bill {ack.eway_bill_no}")
        self.ack = ack


class EwayAlreadyCancelled(Exception):
    """The portal has already withdrawn this bill — success by fetch, the cancellation twin of
    `EwayAlreadyGenerated`. A resumed `cancelling` claim that finds the work done is recorded as
    `cancelled`, never as a refusal."""

    def __init__(self, ack: EwayCancelAck) -> None:
        super().__init__(f"already cancelled: e-Way bill {ack.eway_bill_no}")
        self.ack = ack


@runtime_checkable
class EwayClient(Protocol):
    """The contract `app/services.py` depends on — never a concrete portal.

    A `Protocol` (structural) rather than an ABC, so an adapter and a test double satisfy it by
    shape. The same precedent `IrpClient` and `ImsClient` follow.
    """

    name: str

    def generate(self, payload: EwayPayload) -> EwayAck:
        """Raise a bill for one consignment and return its number.

        Raises `EwayRejected` when the portal refuses the contents, `EwayUnavailable` when it
        cannot be reached, and `EwayAlreadyGenerated` (carrying the bill it already issued) when
        `payload.idempotency_key` names a generation it already holds. Those three are the whole
        error surface; anything else is a bug.
        """
        ...

    def cancel(
        self, *, eway_bill_no: str, reason_code: str, remark: str, at: datetime
    ) -> EwayCancelAck:
        """Withdraw a bill, inside the portal's 24-hour window.

        Raises `EwayRejected`, `EwayUnavailable`, and `EwayAlreadyCancelled` when the portal
        reports the bill was already withdrawn.
        """
        ...
