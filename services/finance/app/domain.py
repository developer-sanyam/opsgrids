"""Pure finance algorithms and vocabularies — no I/O, no session, no ORM writes.

Everything a receivable rollup needs comes through here, so the KPIs, the AR-aging bands and
the customer standings all derive from one set of rules and cannot drift apart. This mirrors the
SPA's `src/data/derive.ts` exactly (same bands, same open-invoice test, same net-revenue rule),
because the live screens and the fixtures must agree to the paisa.

Amounts stay in integer minor units throughout; nothing here divides by 100.

The block of *representative* figures at the bottom — the cash balance, the KPI spark-lines and
deltas, the six-month cashflow, and the trial-balance accounts — are not derivable from an AR
book alone (they need a cash ledger and a general ledger this slice does not own). They are
carried as constants that match the approved SPA fixtures so the Overview and Ledger screens
render identically live, and are the obvious seam a real GL integration replaces later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

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
