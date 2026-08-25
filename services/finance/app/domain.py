"""Pure finance algorithms and vocabularies — no I/O, no session, no ORM writes.

Everything a receivable rollup needs comes through here, so the KPIs, the AR-aging bands and
the customer standings all derive from one set of rules and cannot drift apart. This mirrors the
SPA's `src/data/derive.ts` exactly (same bands, same open-invoice test, same net-revenue rule),
because the live screens and the fixtures must agree to the paisa.

**The GST rules live here too** — GSTIN parsing and its checksum, the state-code table, the
place-of-supply test and the CGST/SGST/IGST split. They are pure functions over integer minor
units on purpose: a tax split is the one calculation on the platform that must foot *exactly*,
because the sum of the three components is what a GSTR-1 return and an e-invoice payload each
declare independently, and a paisa of drift between them is a filing mismatch a human has to
reconcile by hand. Nothing here divides by 100, and nothing here is a float.

The block of *representative* figures at the bottom — the cash balance, the KPI spark-lines and
deltas, the six-month cashflow, and the trial-balance accounts — are not derivable from an AR
book alone (they need a cash ledger and a general ledger this slice does not own). They are
carried as constants that match the approved SPA fixtures so the Overview and Ledger screens
render identically live, and are the obvious seam a real GL integration replaces later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

# --------------------------------------------------------------------- vocabularies

INVOICE_STATUSES: frozenset[str] = frozenset({"draft", "sent", "paid", "overdue", "void"})
DOCUMENT_TYPES: frozenset[str] = frozenset({"invoice", "credit_note"})
PAYMENT_METHODS: frozenset[str] = frozenset({"bank_transfer", "upi", "card", "cheque", "cash"})

#: The demo clock. Invoice statuses are frozen against this at seed time, and the Overview's
#: date-derived rollups (aging, "revenue this period") anchor to it, so the live screens match
#: the approved fixtures regardless of the wall-clock day the seed or the request runs.
REFERENCE_DATE: date = date(2026, 8, 24)
PERIOD_MONTH: str = "2026-08"
PERIOD_LABEL: str = "August 2026"

#: How a supply is classified for GST. Drives both the tax split and whether the document is
#: reportable to the Invoice Registration Portal at all.
#:
#:   b2b     — a registered recipient. Reportable.
#:   b2c     — an unregistered recipient. **Not** reportable to the IRP (a dynamic QR code is a
#:             separate obligation, out of scope for this slice).
#:   export  — out of India. Always inter-state (IGST), with or without payment of tax.
#:   sez     — a Special Economic Zone unit or developer. Always inter-state by statute, even
#:             when the SEZ sits in the seller's own state (IGST Act s. 7(5)(b)).
SUPPLY_TYPES: frozenset[str] = frozenset({"b2b", "b2c", "export", "sez"})

#: Supplies that are inter-state *whatever* the two state codes say. The SEZ case is the one
#: that catches people out: same state, still IGST.
INTERSTATE_SUPPLY_TYPES: frozenset[str] = frozenset({"export", "sez"})

#: Supply types an e-invoice (IRN) is required for. B2C is deliberately absent.
EINVOICE_SUPPLY_TYPES: frozenset[str] = frozenset({"b2b", "export", "sez"})

#: The lifecycle of one filing attempt. `pending` is an attempt recorded before an answer came
#: back; `filed` carries an IRN; `failed` carries the portal's error; `cancelled` records that a
#: previously registered IRN was withdrawn (the IRP allows this within 24 hours).
FILING_STATUSES: frozenset[str] = frozenset({"pending", "filed", "failed", "cancelled"})

#: The statutory GST rate bands. 0.25 % and 3 % are the precious-stones and precious-metals
#: bands — easy to forget, and wrong to reject.
GST_RATE_BANDS: frozenset[Decimal] = frozenset(
    Decimal(band) for band in ("0", "0.25", "3", "5", "12", "18", "28")
)

#: `document_type` → the IRP's own document-type code. A credit note is reportable exactly as an
#: invoice is; it is the correction mechanism GST recognises, which is why the platform's
#: append-only invoice model maps onto the portal with nothing left over.
IRP_DOCUMENT_TYPES: dict[str, str] = {"invoice": "INV", "credit_note": "CRN"}

#: The portal's cancellation reason codes. There are four, and no free-text alternative.
IRN_CANCELLATION_REASONS: dict[str, str] = {
    "1": "Duplicate",
    "2": "Data entry mistake",
    "3": "Order cancelled",
    "4": "Others",
}

#: How long after registration an IRN may be withdrawn. Past it, the only correction is a credit
#: note — which is the platform's native shape anyway, so nothing is lost by the window closing.
IRN_CANCELLATION_WINDOW: timedelta = timedelta(hours=24)


# --------------------------------------------------------------------- GST identity
# A GSTIN is not a string, it is a structured identifier with a checksum. Treating it as a string
# is how a transposed digit reaches a return and comes back as a mismatch weeks later, so it is
# parsed into a value object at every boundary and stored only after it has been parsed.

#: GST state codes → the state or union territory they denote. The first two characters of every
#: GSTIN, and the vocabulary `place_of_supply` is drawn from.
#:
#: `25` (Daman and Diu) and `28` (undivided Andhra Pradesh) are **legacy**: both were retired by
#: reorganisations, and both still appear on registrations issued before them. They are listed so
#: a historical GSTIN parses rather than being refused as malformed.
GST_STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    # Suffixed so the name→code map has no ambiguous key: an unqualified "Daman and Diu" today
    # means the merged union territory, 26.
    "25": "Daman and Diu (legacy)",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "28": "Andhra Pradesh (undivided)",  # legacy — see 37
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
    "99": "Centre Jurisdiction",
}

#: Name → code, for the free-text `state` a customer record carries. Lower-cased and stripped of
#: punctuation on both sides so "Tamil Nadu", "tamil nadu" and "TAMIL NADU" all resolve.
_STATE_CODE_BY_NAME: dict[str, str] = {
    # Built from the table above, plus the everyday spellings that are not the statutory name.
    **{name.lower(): code for code, name in GST_STATE_CODES.items()},
    "andhra pradesh (undivided)": "28",
    "orissa": "21",
    "pondicherry": "34",
    "uttaranchal": "05",
    "new delhi": "07",
    "nct of delhi": "07",
    "jammu & kashmir": "01",
    "dadra and nagar haveli": "26",
    "daman and diu": "26",
    "andaman & nicobar islands": "35",
}

#: The base-36 alphabet the GSTIN check digit is computed over. Order is load-bearing: the
#: character's index *is* its value.
_GSTIN_ALPHABET: str = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: Two state digits, a ten-character PAN (5 letters, 4 digits, 1 letter), a registration serial
#: for that PAN within the state, a fixed `Z`, and the check digit.
#:
#: The fixed `Z` is deliberate. It is `Z` on every regular registration; the exceptions are UIN
#: holders (embassies, UN bodies) and OIDAR registrations, neither of which is a customer this
#: product bills today. Refusing them loudly is better than accepting an identifier the rest of
#: the pipeline would then have to special-case — and the checksum catches an ordinary typo in
#: that position anyway.
_GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


class GstinError(ValueError):
    """A GSTIN that is not one. Carries a reason a person can act on."""


@dataclass(frozen=True, slots=True)
class Gstin:
    """A parsed, checksum-verified GST identification number.

    Constructed only by `parse_gstin`, so holding one of these is proof the number is
    well-formed, names a real state, and carries the right check digit.
    """

    value: str
    state_code: str
    pan: str
    #: The registration serial for this PAN within this state — `1` for a first registration.
    entity_code: str
    check_digit: str

    @property
    def state_name(self) -> str:
        return GST_STATE_CODES[self.state_code]

    def __str__(self) -> str:
        return self.value


def gstin_check_digit(base: str) -> str:
    """The 15th character of a GSTIN, computed from the first fourteen.

    GSTN's own algorithm, and worth stating precisely because every plausible-looking variant of
    it produces a different answer. Walking the base **right to left** with the multiplier
    starting at 2 and alternating 1/2, each product is folded (quotient plus remainder over 36)
    before it is summed — the fold is what makes it a Luhn-style check over a base-36 alphabet
    rather than a plain weighted sum, and it is what catches a transposition.

    Raises `GstinError` if `base` is not fourteen characters from the GSTIN alphabet.
    """
    if len(base) != 14:
        raise GstinError("a GSTIN check digit is computed over exactly 14 characters")

    factor = 2
    total = 0
    for char in reversed(base):
        index = _GSTIN_ALPHABET.find(char)
        if index < 0:
            raise GstinError(f"{char!r} is not a character a GSTIN may contain")
        product = factor * index
        factor = 1 if factor == 2 else 2
        total += (product // 36) + (product % 36)
    return _GSTIN_ALPHABET[(36 - (total % 36)) % 36]


def normalise_gstin(raw: str) -> str:
    """Upper-case and strip the spaces and hyphens people paste in. No validation."""
    return re.sub(r"[\s\-]", "", raw).upper()


def parse_gstin(raw: str) -> Gstin:
    """Parse and verify a GSTIN, or raise `GstinError` saying which of the four checks failed.

    The four, in the order a wrong number usually fails them: length, structure, a state code
    that exists, and the checksum. The checksum is last because it is the one that catches the
    errors the eye does not — a transposed pair, one digit off.
    """
    value = normalise_gstin(raw)
    if len(value) != 15:
        raise GstinError(f"a GSTIN is 15 characters; {value!r} is {len(value)}")
    if not _GSTIN_PATTERN.match(value):
        raise GstinError(
            f"{value!r} is not shaped like a GSTIN "
            "(two state digits, a PAN, a registration serial, 'Z', a check digit)"
        )

    state_code = value[:2]
    if state_code not in GST_STATE_CODES:
        raise GstinError(f"{state_code!r} is not a GST state code")

    expected = gstin_check_digit(value[:14])
    if value[14] != expected:
        raise GstinError(
            f"{value!r} fails its checksum: the check digit should be {expected!r}, not "
            f"{value[14]!r}"
        )

    return Gstin(
        value=value,
        state_code=state_code,
        pan=value[2:12],
        entity_code=value[12],
        check_digit=value[14],
    )


def is_valid_gstin(raw: str) -> bool:
    """`True` iff `raw` parses. For call sites that want a predicate rather than the reason."""
    try:
        parse_gstin(raw)
    except GstinError:
        return False
    return True


def state_code_for_name(name: str | None) -> str | None:
    """Best-effort state name → GST state code, for the free-text `state` on a customer row.

    Returns None rather than guessing when the name is not recognised: an unknown state must
    become a missing place of supply that somebody fixes, never a wrong one that quietly picks a
    tax head.
    """
    if not name:
        return None
    key = re.sub(r"[.,]", "", name.strip().lower())
    return _STATE_CODE_BY_NAME.get(key)


def financial_year(on: date) -> str:
    """India's financial year for a date, as the IRP writes it: April to March, `2026-27`.

    Part of the IRN input, so it is a domain rule rather than a formatting detail.
    """
    start = on.year if on.month >= 4 else on.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


# --------------------------------------------------------------------- the tax split
# Integer arithmetic end to end. The invariant every function here preserves: the components sum
# to the total, exactly, at every level — line, document, and return.


@dataclass(frozen=True, slots=True)
class TaxSplit:
    """One taxable amount's GST, split across the heads it is payable under.

    Exactly one of the two shapes is ever populated: CGST+SGST for an intra-state supply, IGST
    for an inter-state one. Both are carried on the same object so a caller never has to ask
    which shape it got before summing.
    """

    cgst_minor: int = 0
    sgst_minor: int = 0
    igst_minor: int = 0

    @property
    def total_minor(self) -> int:
        return self.cgst_minor + self.sgst_minor + self.igst_minor

    def __add__(self, other: TaxSplit) -> TaxSplit:
        """Component-wise, so a document's split is `sum(line splits)` and foots by construction."""
        return TaxSplit(
            cgst_minor=self.cgst_minor + other.cgst_minor,
            sgst_minor=self.sgst_minor + other.sgst_minor,
            igst_minor=self.igst_minor + other.igst_minor,
        )


ZERO_TAX = TaxSplit()


def gst_amount_minor(taxable_minor: int, rate_pct: Decimal) -> int:
    """Tax on a taxable amount, in minor units, rounded half-up **once**.

    Exact via `Decimal`: a rate is a rate (12, 18, 0.25) and a float would make 18 % of a large
    receivable land a paisa out often enough to matter. `ROUND_HALF_UP` on a `Decimal` rounds
    away from zero, so a credit note's negative tax rounds symmetrically with the invoice's.
    """
    exact = Decimal(taxable_minor) * rate_pct / Decimal(100)
    return int(exact.to_integral_value(rounding=ROUND_HALF_UP))


def split_gst(taxable_minor: int, rate_pct: Decimal, *, intra_state: bool) -> TaxSplit:
    """Split the GST on one taxable amount into the heads it is payable under.

    Intra-state, the rate is levied half as CGST (central) and half as SGST (state). The halves
    are **not** each rounded: the total is computed once and then divided, with the odd paisa
    going to SGST, so `cgst + sgst == gst_amount_minor(...)` for every input rather than for the
    even ones. Rounding each half independently is the bug this shape exists to prevent — it
    puts the document a paisa out from its own lines about half the time on odd rates.

    The odd paisa goes to SGST in both directions of sign, so a credit note reverses an invoice
    head for head instead of leaving a paisa stranded in CGST.
    """
    total = gst_amount_minor(taxable_minor, rate_pct)
    if not intra_state:
        return TaxSplit(igst_minor=total)
    half = abs(total) // 2
    cgst = -half if total < 0 else half
    return TaxSplit(cgst_minor=cgst, sgst_minor=total - cgst)


def is_intra_state(*, supply_type: str, seller_state_code: str, place_of_supply: str) -> bool:
    """Whether a supply attracts CGST+SGST rather than IGST.

    Two state codes being equal is the ordinary test, but it is not the whole rule: an export or
    a supply to a Special Economic Zone is inter-state **by statute** even when the recipient
    sits in the seller's own state. That is the case a naive `seller == buyer` comparison gets
    wrong, and it is wrong in the expensive direction — tax paid under the wrong head is not a
    rounding difference, it is a refund claim.
    """
    if supply_type in INTERSTATE_SUPPLY_TYPES:
        return False
    return bool(seller_state_code) and seller_state_code == place_of_supply


def requires_einvoice(*, supply_type: str, document_type: str) -> bool:
    """Whether this document must carry an IRN.

    B2C is out: e-invoicing covers B2B, exports and SEZ supplies. Credit notes are in — a
    correction against a reported invoice is itself reported.
    """
    return supply_type in EINVOICE_SUPPLY_TYPES and document_type in IRP_DOCUMENT_TYPES


def cancellation_window_open(ack_date: datetime, at: datetime) -> bool:
    """Whether a registered IRN may still be withdrawn at `at`.

    Enforced here, against the platform's own filing ledger, rather than left to the portal to
    refuse: the answer is knowable without a network, and knowing it locally is what lets the UI
    offer "cancel" or "raise a credit note" correctly instead of offering both and failing one.
    """
    return at - ack_date <= IRN_CANCELLATION_WINDOW


def normalise_hsn(raw: str | None) -> str | None:
    """An HSN (goods) or SAC (services) code, or None.

    Accepts 4, 6 or 8 digits — the lengths the schedule is published at. Six is the mandatory
    minimum once a taxpayer's aggregate turnover passes ₹5 crore, which is the same threshold
    e-invoicing binds at, so in practice everything this service files carries at least six.
    """
    if raw is None:
        return None
    code = re.sub(r"[\s\-]", "", raw)
    if not code:
        return None
    if not code.isdigit() or len(code) not in (4, 6, 8):
        raise ValueError(f"{raw!r} is not an HSN/SAC code (4, 6 or 8 digits)")
    return code


# --------------------------------------------------------------------- invoice facts


@dataclass(frozen=True, slots=True)
class InvoiceFacts:
    """The minimum an AR rollup needs from one document. Built by the selectors from a row plus
    the cash applied to it, then handed to the pure functions below."""

    document_type: str
    status: str
    issued_date: date
    due_date: date
    total_minor: int
    amount_paid_minor: int


def outstanding_minor(f: InvoiceFacts) -> int:
    """What is still owed on a document: total less what has been paid, never below zero."""
    return max(0, f.total_minor - f.amount_paid_minor)


def is_open_invoice(f: InvoiceFacts) -> bool:
    """A real invoice (not a credit note) that still carries a receivable."""
    return f.document_type == "invoice" and f.status in ("sent", "overdue")


def days_overdue(due_date: date, today: date) -> int:
    """Days past due as of `today`; 0 or negative means not yet due."""
    return (today - due_date).days


def total_outstanding(facts: list[InvoiceFacts]) -> int:
    """Total receivable outstanding across all open invoices."""
    return sum(outstanding_minor(f) for f in facts if is_open_invoice(f))


def total_overdue(facts: list[InvoiceFacts]) -> int:
    """Outstanding on invoices already past their due date."""
    return sum(outstanding_minor(f) for f in facts if is_open_invoice(f) and f.status == "overdue")


def net_revenue_for_month(facts: list[InvoiceFacts], month: str) -> int:
    """Net revenue recognised in a calendar month (`YYYY-MM`): issued invoices less credit
    notes. Drafts and voids are not recognised; credit-note totals are negative, so a plain sum
    of signed totals gives the net figure."""
    return sum(
        f.total_minor
        for f in facts
        if f.status not in ("draft", "void") and f.issued_date.isoformat().startswith(month)
    )


@dataclass(frozen=True, slots=True)
class _Band:
    key: str
    label: str
    lo: float
    hi: float


_AGING_BANDS: tuple[_Band, ...] = (
    # Labels carry the en-dash the approved SPA fixtures display (hence the per-line RUF001 skip).
    _Band("current", "Current", float("-inf"), 0),
    _Band("d1_30", "1–30 days", 1, 30),  # noqa: RUF001
    _Band("d31_60", "31–60 days", 31, 60),  # noqa: RUF001
    _Band("d61_90", "61–90 days", 61, 90),  # noqa: RUF001
    _Band("d90p", "90+ days", 91, float("inf")),
)


@dataclass(frozen=True, slots=True)
class AgingBucketData:
    key: str
    label: str
    amount_minor: int
    count: int


def aging_buckets(facts: list[InvoiceFacts], today: date) -> list[AgingBucketData]:
    """AR aging: open receivable split by how far past due each invoice is."""
    out: list[AgingBucketData] = []
    for band in _AGING_BANDS:
        rows = [
            f
            for f in facts
            if is_open_invoice(f) and band.lo <= days_overdue(f.due_date, today) <= band.hi
        ]
        out.append(
            AgingBucketData(
                key=band.key,
                label=band.label,
                amount_minor=sum(outstanding_minor(f) for f in rows),
                count=len(rows),
            )
        )
    return out


def customer_standing(receivables_minor: int, overdue_minor: int) -> str:
    """A customer's account standing, rolled up from their invoices."""
    if overdue_minor > 0:
        return "overdue"
    if receivables_minor > 0:
        return "open"
    return "clear"


def derive_invoice_status(
    *,
    document_type: str,
    status_override: str | None,
    due_date: date,
    total_minor: int,
    paid_minor: int,
    today: date,
) -> str:
    """The status an invoice finalizes with — the SPA fixtures' rule, server-side.

    An explicit override (draft, void) wins. A credit note is an issued correction with no
    paid/overdue lifecycle of its own. Otherwise: paid once settled, overdue once past due,
    else sent.
    """
    if status_override:
        return status_override
    if document_type == "credit_note":
        return "sent"
    if paid_minor >= total_minor:
        return "paid"
    return "overdue" if due_date < today else "sent"


# --------------------------------------------------------------------- representative figures
# Not derivable from an AR book (they need a cash ledger and a general ledger). Carried as
# strongly-typed constants matching the approved SPA fixtures; the seam a real GL integration
# replaces. Held as dataclasses (not schema types — `domain` sits below `schemas`) so the
# selectors map them onto the wire contract with no loose dicts to mistype.


def _paise(rupees: int) -> int:
    return rupees * 100


@dataclass(frozen=True, slots=True)
class KpiTrend:
    series: list[float]
    delta_pct: float


@dataclass(frozen=True, slots=True)
class CashflowRow:
    label: str
    inflow_minor: int
    outflow_minor: int


@dataclass(frozen=True, slots=True)
class LedgerRow:
    code: str
    name: str
    type: str
    debit_minor: int
    credit_minor: int


#: Bank + cash on hand across accounts — a booked figure no invoice can imply.
CASH_BALANCE_MINOR: int = _paise(18_640_000)

#: Trend spark-line + delta for each overview KPI, matching the SPA fixtures.
KPI_TRENDS: dict[str, KpiTrend] = {
    "revenue": KpiTrend([42, 51, 55, 49, 71, 63, 70], 8.4),
    "receivables": KpiTrend([58, 61, 64, 69, 66, 72, 74], 6.1),
    "overdue": KpiTrend([12, 9, 14, 11, 16, 19, 21], 12.7),
    "cash": KpiTrend([141, 152, 149, 168, 172, 179, 186], 3.9),
}

#: Six months of cash in / out for the overview area chart (representative aggregate).
CASHFLOW: list[CashflowRow] = [
    CashflowRow("Mar", _paise(3_820_000), _paise(2_610_000)),
    CashflowRow("Apr", _paise(5_240_000), _paise(3_180_000)),
    CashflowRow("May", _paise(6_180_000), _paise(3_460_000)),
    CashflowRow("Jun", _paise(4_920_000), _paise(3_890_000)),
    CashflowRow("Jul", _paise(7_150_000), _paise(4_120_000)),
    CashflowRow("Aug", _paise(5_860_000), _paise(4_530_000)),
]

#: P&L / trial-balance accounts, FY-to-date. Debits and credits are booked figures.
LEDGER_PERIOD_LABEL: str = "FY 2026–27 · to date"  # noqa: RUF001 - display string, SPA fixture
LEDGER_ACCOUNTS: list[LedgerRow] = [
    LedgerRow("4000", "Sales — Goods", "revenue", 0, _paise(48_200_000)),
    LedgerRow("4100", "Sales — Services", "revenue", 0, _paise(9_640_000)),
    LedgerRow("4200", "Freight recovered", "revenue", 0, _paise(1_820_000)),
    LedgerRow("4900", "Sales returns & credit notes", "contra_revenue", _paise(1_240_000), 0),
    LedgerRow("5000", "Cost of goods sold", "expense", _paise(31_500_000), 0),
    LedgerRow("5100", "Salaries & wages", "expense", _paise(8_900_000), 0),
    LedgerRow("5200", "Rent & utilities", "expense", _paise(2_150_000), 0),
    LedgerRow("5300", "Logistics & freight", "expense", _paise(3_420_000), 0),
    LedgerRow("5400", "Marketing & sales", "expense", _paise(1_760_000), 0),
    LedgerRow("5500", "Depreciation", "expense", _paise(980_000), 0),
]
