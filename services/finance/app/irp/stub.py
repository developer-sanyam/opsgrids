"""A deterministic Invoice Registration Portal — real everywhere except the wire.

The same bargain payment's Razorpay adapter strikes, for the same reason: there are no NIC or GSP
credentials in development, and there is no sandbox a CI run may depend on, but the *state
machine* around the portal is where the bugs live and it must be exercised today.

What is **real** here:

* **The IRN.** Computed with the government's own formula — `SHA-256(supplier GSTIN + document
  type + document number + financial year)` — so the identifier this stub returns is the identifier
  the real portal would return for the same document, character for character. That is not a
  cosmetic detail: it is what makes "the same document filed twice yields the same IRN" true here
  in the same way it is true in production.
* **The validations.** A document is refused for the reasons the portal refuses one: an invalid
  GSTIN, a missing HSN, a place of supply that is not a state, tax charged under the wrong head
  for the supply, lines that do not sum to the document. Each refusal is a numbered code, and the
  code is what reaches `einvoice_filings.error_code`.
* **The acknowledgement and the QR.** The acknowledgement number is derived from the IRN and the
  QR payload is a three-segment JWS-shaped string over the exact fields the printed code carries,
  so everything downstream — storage, the read model, the eventual PDF — handles the real shapes.

What is **stubbed** — the one seam:

* **The outbound HTTP.** `register` and `cancel` compute their answer instead of asking. No socket
  is opened and no credential is needed, which a test asserts.
* **The QR signature.** The real code is signed by the portal with NIC's key, which nothing
  outside NIC can produce. This one is an HMAC under a local key: the right *shape*, honestly not
  the right signature, and labelled as such inside the payload so a stubbed QR can never be
  mistaken for a registered one.

**On the error codes.** They mirror the shape of the NIC list — a stable numeric code plus a
message — and use its numbering where this platform is confident of it. The contract the service
layer depends on is that a rejection carries a code it can store and a message a human can act
on, not that this stub reproduces the portal's catalogue exactly; a real client swaps in and the
codes get better without the state machine changing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime
from decimal import Decimal

from app import domain
from app.irp.base import IrpAck, IrpCancelAck, IrpInvoicePayload, IrpRejected

PORTAL = "stub-irp"

#: The stand-in signing key for the QR. Not a secret and not pretending to be one — the real QR is
#: signed by NIC, and nothing outside NIC can produce that signature.
_STUB_QR_KEY = b"stub-irp-qr-signing-key"

# Error codes, in the order a wrong document usually trips them.
ERR_INVALID_SUPPLIER_GSTIN = "3028"
ERR_INVALID_RECIPIENT_GSTIN = "3029"
ERR_RECIPIENT_GSTIN_REQUIRED = "2233"
ERR_INVALID_PLACE_OF_SUPPLY = "2265"
ERR_HSN_REQUIRED = "2227"
ERR_TAX_HEAD_MISMATCH = "2172"
ERR_LINES_DO_NOT_FOOT = "2182"
ERR_TOTAL_DOES_NOT_FOOT = "2189"
ERR_NOT_REPORTABLE = "2216"
ERR_NO_LINES = "2231"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class StubIrpClient:
    """An `IrpClient` that answers from arithmetic. Network stubbed; everything else real."""

    name = PORTAL

    # ------------------------------------------------------------------ registration

    def register(self, payload: IrpInvoicePayload) -> IrpAck:
        """Validate the document the way the portal does, then issue its IRN."""
        self._validate(payload)

        irn = compute_irn(
            seller_gstin=payload.seller_gstin,
            document_type=payload.document_type,
            document_number=payload.document_number,
            financial_year=payload.financial_year,
        )
        return IrpAck(
            irn=irn,
            ack_no=_ack_no(irn),
            # The portal stamps its own clock; here the caller's is the clock, which is what
            # makes an attempt reproducible.
            ack_date=payload.generated_at,
            qr_payload=_qr_payload(payload, irn),
        )

    def cancel(self, *, irn: str, reason_code: str, remark: str, at: datetime) -> IrpCancelAck:
        """Withdraw an IRN.

        The 24-hour window is **not** checked here on purpose: this stub holds no record of when
        the IRN was issued, and neither would a client for a portal that answers from its own
        state. The window is enforced against the platform's own filing ledger before the call is
        made, where it is testable without a portal at all.
        """
        if reason_code not in domain.IRN_CANCELLATION_REASONS:
            raise IrpRejected(
                ERR_NOT_REPORTABLE,
                f"{reason_code!r} is not a cancellation reason code the portal accepts",
            )
        if len(irn) != 64:
            raise IrpRejected(ERR_NOT_REPORTABLE, "IRN is not a 64-character reference")
        return IrpCancelAck(irn=irn, cancel_date=at)

    # ------------------------------------------------------------------ validation

    def _validate(self, payload: IrpInvoicePayload) -> None:
        """Every check the portal would run, in the order it would run them."""
        if payload.supply_type not in domain.EINVOICE_SUPPLY_TYPES:
            raise IrpRejected(
                ERR_NOT_REPORTABLE,
                f"supply type {payload.supply_type!r} is not reportable as an e-invoice",
            )
        if payload.document_type not in domain.IRP_DOCUMENT_TYPES.values():
            raise IrpRejected(
                ERR_NOT_REPORTABLE,
                f"document type {payload.document_type!r} is not one the portal registers",
            )

        if not domain.is_valid_gstin(payload.seller_gstin):
            raise IrpRejected(
                ERR_INVALID_SUPPLIER_GSTIN,
                f"supplier GSTIN {payload.seller_gstin!r} is invalid",
            )

        # An export has no recipient GSTIN; every other reportable supply must have one.
        if payload.supply_type == "export":
            pass
        elif not payload.buyer_gstin:
            raise IrpRejected(
                ERR_RECIPIENT_GSTIN_REQUIRED,
                f"recipient GSTIN is required for a {payload.supply_type} supply",
            )
        elif not domain.is_valid_gstin(payload.buyer_gstin):
            raise IrpRejected(
                ERR_INVALID_RECIPIENT_GSTIN,
                f"recipient GSTIN {payload.buyer_gstin!r} is invalid",
            )

        if payload.place_of_supply not in domain.GST_STATE_CODES:
            raise IrpRejected(
                ERR_INVALID_PLACE_OF_SUPPLY,
                f"place of supply {payload.place_of_supply!r} is not a GST state code",
            )

        if not payload.lines:
            raise IrpRejected(ERR_NO_LINES, "a document must carry at least one line")

        intra = domain.is_intra_state(
            supply_type=payload.supply_type,
            seller_state_code=payload.seller_state_code,
            place_of_supply=payload.place_of_supply,
        )

        for line in payload.lines:
            if not line.hsn_sac:
                raise IrpRejected(
                    ERR_HSN_REQUIRED,
                    f"line {line.serial_no} carries no HSN/SAC code",
                )
            # Tax under the wrong head is the expensive error: it is a refund claim, not a
            # rounding difference. The portal recomputes and refuses; so does this.
            if intra and line.igst_minor:
                raise IrpRejected(
                    ERR_TAX_HEAD_MISMATCH,
                    f"line {line.serial_no} charges IGST on an intra-state supply",
                )
            if not intra and (line.cgst_minor or line.sgst_minor):
                raise IrpRejected(
                    ERR_TAX_HEAD_MISMATCH,
                    f"line {line.serial_no} charges CGST/SGST on an inter-state supply",
                )
            expected = domain.split_gst(line.taxable_minor, line.rate_pct, intra_state=intra)
            actual = domain.TaxSplit(line.cgst_minor, line.sgst_minor, line.igst_minor)
            if not payload.reverse_charge and actual != expected:
                raise IrpRejected(
                    ERR_TAX_HEAD_MISMATCH,
                    f"line {line.serial_no} tax does not match {line.rate_pct}% of its "
                    "taxable value",
                )
            if payload.reverse_charge and actual.total_minor:
                raise IrpRejected(
                    ERR_TAX_HEAD_MISMATCH,
                    f"line {line.serial_no} charges tax on a reverse-charge supply",
                )

        if sum(line.taxable_minor for line in payload.lines) != payload.taxable_minor:
            raise IrpRejected(
                ERR_LINES_DO_NOT_FOOT,
                "the lines' taxable values do not sum to the document's taxable value",
            )

        tax = payload.cgst_minor + payload.sgst_minor + payload.igst_minor
        if payload.taxable_minor + tax != payload.total_minor:
            raise IrpRejected(
                ERR_TOTAL_DOES_NOT_FOOT,
                "taxable value plus tax does not equal the document total",
            )


# --------------------------------------------------------------------- the real formulas


def compute_irn(
    *, seller_gstin: str, document_type: str, document_number: str, financial_year: str
) -> str:
    """The Invoice Reference Number, by the government's formula.

    `SHA-256(supplier GSTIN + document type + document number + financial year)`, hex, 64
    characters. Deterministic by design and by statute: the same document can only ever have one
    IRN, which is how the portal detects a duplicate and how a retry is safe.
    """
    material = f"{seller_gstin}{document_type}{document_number}{financial_year}"
    return hashlib.sha256(material.encode()).hexdigest()


def _ack_no(irn: str) -> str:
    """A 16-digit acknowledgement number, derived from the IRN so a retry reproduces it."""
    return f"{int(irn[:16], 16) % 10**16:016d}"


def _qr_payload(payload: IrpInvoicePayload, irn: str) -> str:
    """The signed QR, JWS-shaped: header, payload, signature, dot-separated and base64url.

    The payload carries exactly the fields the printed code carries, in the portal's own key
    names, so whatever renders the invoice later reads the real shape. `Stub` is stamped in the
    header: this signature is an HMAC under a local key, not NIC's, and must never be mistaken
    for a registered one.
    """
    header = {"alg": "HS256", "typ": "JWT", "stub": True, "portal": PORTAL}
    body = {
        "SellerGstin": payload.seller_gstin,
        "BuyerGstin": payload.buyer_gstin or "URP",
        "DocNo": payload.document_number,
        "DocTyp": payload.document_type,
        "DocDt": payload.document_date.strftime("%d/%m/%Y"),
        # The printed code shows rupees, so this is the one place minor units are converted —
        # at the very edge, as a string, exactly as the schema writes it.
        "TotInvVal": _rupees(payload.total_minor),
        "ItemCnt": len(payload.lines),
        "MainHsnCode": payload.lines[0].hsn_sac if payload.lines else None,
        "Irn": irn,
        "IrnDt": payload.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
    }
    signing_input = (
        f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url(json.dumps(body, separators=(',', ':')).encode())}"
    )
    signature = hmac.new(_STUB_QR_KEY, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def _rupees(minor: int) -> str:
    """Minor units to the rupee string the schema is written in. The last conversion, at the
    edge, and the only one anywhere in this service."""
    return str((Decimal(minor) / Decimal(100)).quantize(Decimal("0.01")))


def build_client() -> StubIrpClient:
    """Construct the configured IRP client. Only the stub exists today; a NIC or GSP client would
    be selected here, and because it satisfies the same `Protocol`, nothing downstream changes."""
    return StubIrpClient()
