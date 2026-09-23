"""The IRP seam — one `Protocol` every Invoice Registration Portal client satisfies.

E-invoicing in India is not one integration, it is a *choice* of integrations: the NIC portal
directly, or one of several GSPs (GST Suvidha Providers) reselling it with their own auth and
their own rate limits. They differ in credentials, in envelope, and in almost nothing else — the
document they carry is the same government schema, and the answer they return is the same IRN,
acknowledgement and signed QR.

So the seam is drawn where the difference is: `IrpClient` speaks the *document*, and each
implementation owns the wire. The service layer holds an `IrpClient`; which concrete one it is —
the deterministic stub this slice ships, a NIC client, a GSP client — is a wiring decision in
`app/deps.py`, not a change to any rule. This is the same shape payment's provider seam uses, and
for the same reason: designing against the constrained case first yields an abstraction the
others fit inside.

Three properties are deliberately encoded here rather than left to each implementation:

* **Registration can be refused, and a refusal is data.** The portal answers a malformed document
  with a numbered error, not a transport failure, so `IrpRejected` carries the portal's own code
  and message. That code is what gets written to `einvoice_filings.error_code` and what a support
  conversation quotes.
* **The clock is an input.** `generated_at` travels on the payload rather than being read inside
  an implementation, so a filing is a pure function of what was sent — which is what makes the
  stub deterministic and the whole state machine testable without a network.
* **Amounts are integer minor units, all the way to the wire.** The government schema is written
  in rupees with two decimal places; converting is the *adapter's* job, at the last moment, and
  never the service layer's. Nothing above this line divides by 100.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable


class IrpRejected(Exception):
    """The portal refused the document. Carries the portal's own numbered error.

    Not a transport failure: the request arrived, was understood, and was rejected on its
    contents. The caller records a `failed` filing row carrying `code` and `message` and does
    **not** retry the identical document — retrying a 2233 forever is how an integration goes
    quiet and a deadline passes.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class IrpUnavailable(Exception):
    """The portal could not be reached or did not answer in time.

    Distinct from `IrpRejected` because the response is opposite: a rejection is final until the
    document changes, an outage is retryable with the identical document.
    """


class IrpDuplicate(Exception):
    """The portal already holds an IRN for this document — success wearing a refusal's shape.

    A document number is the portal's own idempotency key (`compute_irn` is a pure function of
    it), so the real NIC/GSP answer to a second registration of the same document is its numbered
    "Duplicate IRN" error (`2150`, the code `EInvoiceFiling.error_code` documents), carrying the
    IRN it already issued. That is exactly the shape a resumed call takes after a crash between
    `call_irp_register` and `record_registration_result` (D3): the platform's own claim row never
    saw an answer, but the portal did the work anyway. A real adapter raises this — carrying the
    ack the portal returned instead of a fresh one — rather than `IrpRejected`, so the caller
    records the resumed attempt as `filed`, not `failed`. `StubIrpClient.register` never needs to
    raise it: computing the same IRN from the same document is what makes calling it again safe
    without a portal round trip to ask.
    """

    def __init__(self, ack: IrpAck) -> None:
        super().__init__(f"duplicate: IRN already issued as {ack.irn}")
        self.ack = ack


class IrpAlreadyCancelled(Exception):
    """The portal has already withdrawn this IRN — success wearing a refusal's shape.

    Cancelling is **not** idempotent by document the way registering is: it is time-boxed, needs a
    reason, and a repeat is answered with an error. That is exactly the shape a resumed call takes
    after a crash between `call_irp_cancel` and `record_irn_cancellation` (D3): the platform's own
    `cancelling` claim never saw the answer, but the portal did the work. A real adapter maps its
    "IRN already cancelled" error onto this, carrying the cancellation it already holds, so the
    resumed attempt is recorded as the `cancelled` it is — success-by-fetch, never a `failed`.
    """

    def __init__(self, ack: IrpCancelAck) -> None:
        super().__init__(f"already cancelled: IRN {ack.irn} was withdrawn at {ack.cancel_date}")
        self.ack = ack


@dataclass(frozen=True, slots=True)
class IrpLine:
    """One line of the document as the portal validates it.

    `taxable_minor` is the assessable value GST is computed on. The three tax components are the
    platform's own split, sent rather than inferred, because the portal recomputes them and
    rejects a document whose arithmetic disagrees with its own (error 2172) — which is exactly
    the check the platform wants to fail *before* a deadline rather than after.
    """

    serial_no: int
    description: str
    hsn_sac: str | None
    quantity: Decimal
    unit_price_minor: int
    taxable_minor: int
    rate_pct: Decimal
    cgst_minor: int
    sgst_minor: int
    igst_minor: int

    @property
    def total_minor(self) -> int:
        return self.taxable_minor + self.cgst_minor + self.sgst_minor + self.igst_minor


@dataclass(frozen=True, slots=True)
class IrpInvoicePayload:
    """One document, in the shape the portal reasons about.

    Deliberately *not* the government JSON: this is the neutral document, and each adapter maps
    it onto whichever envelope its portal wants. Keeping the mapping inside the adapter is what
    lets a GSP be swapped for NIC without the service layer learning a second vocabulary.
    """

    #: INV | CRN — `domain.IRP_DOCUMENT_TYPES`.
    document_type: str
    document_number: str
    document_date: date
    #: b2b | export | sez. B2C never reaches here; it is not reportable.
    supply_type: str
    reverse_charge: bool

    seller_gstin: str
    seller_legal_name: str
    seller_state_code: str

    #: Null only for an export, where the recipient has no GSTIN.
    buyer_gstin: str | None
    buyer_legal_name: str
    #: The GST state code the supply is taxed in.
    place_of_supply: str

    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    total_minor: int

    #: India's financial year for `document_date`, as the portal writes it: `2026-27`.
    financial_year: str
    #: The moment the caller made this attempt. An input, not a `now()` read inside an adapter —
    #: see the module docstring.
    generated_at: datetime

    lines: list[IrpLine] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class IrpAck:
    """What the portal returns when it registers a document.

    `irn` is the Invoice Reference Number: a 64-character hash, unique for that document across
    India, and the thing the whole obligation reduces to. `qr_payload` is the signed code that
    must be printed on the invoice — kept verbatim, never re-derived, because its signature is
    over exactly those bytes.
    """

    irn: str
    ack_no: str
    ack_date: datetime
    qr_payload: str


@dataclass(frozen=True, slots=True)
class IrpCancelAck:
    """What the portal returns when an IRN is withdrawn.

    Cancellation is only legal within 24 hours of registration, and only in whole — there is no
    partial cancel. After that window the correction is a credit note, which is the platform's
    native shape anyway.
    """

    irn: str
    cancel_date: datetime


@runtime_checkable
class IrpClient(Protocol):
    """The contract `app/services.py` depends on — never a concrete portal.

    A `Protocol` (structural) rather than an ABC so an adapter and a test double satisfy it by
    shape, the same precedent payment's `PaymentProvider` follows.
    """

    name: str

    def register(self, payload: IrpInvoicePayload) -> IrpAck:
        """Register a document and return its IRN.

        Raises `IrpRejected` when the portal refuses the contents, `IrpUnavailable` when it
        cannot be reached, and `IrpDuplicate` when it reports the document is already registered
        — the resumed-after-a-crash case (D3), which a real adapter must map onto `IrpDuplicate`
        rather than `IrpRejected` so it is recorded as the success it is. Those three are the
        whole error surface; anything else is a bug.
        """
        ...

    def cancel(self, *, irn: str, reason_code: str, remark: str, at: datetime) -> IrpCancelAck:
        """Withdraw a registered IRN, inside the portal's 24-hour window.

        Raises `IrpRejected` when the portal refuses (window closed, unknown IRN),
        `IrpUnavailable` when it cannot be reached, and `IrpAlreadyCancelled` when it reports the
        IRN was already withdrawn — the resumed-after-a-crash case (D3), which a real adapter must
        map onto that exception rather than `IrpRejected` so it is recorded as the success it is.
        """
        ...
