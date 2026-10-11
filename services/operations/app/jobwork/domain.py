"""Pure rules for job work (Inventory plan Phase 6) — no I/O, no session.

Three things live here because they are algorithms, not plumbing: the order's state machine, the
reconciliation arithmetic, and the small amount of date maths the challan series and the overdue
test need. Everything is integer milli-units; there is no float anywhere in this module.

**The reconciliation identity.** For every material:

    sent = received + rejected + shortage + outstanding

`outstanding` is what is still at the vendor. An order *balances* when every material's
outstanding is zero **and** the ledger agrees: the net of the `AT_JOB_WORKER` ledger rows posted
under this order, per item, equals the documents' outstanding. The first half catches material
nobody has accounted for; the second catches documents and stock drifting apart (a receipt row
written without its move, or the reverse) — which the stock reconciler cannot see, because it
compares the ledger with the balances, not with a document.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone

#: `JobWorkOrder.status` -> where it may go. `PARTIALLY_RECEIVED -> PARTIALLY_RECEIVED` is not an
#: edge: a second partial receipt leaves the status where it is, and the service skips the move.
#: Nothing leaves `CLOSED` or `CANCELLED`. `ISSUED` and later cannot be cancelled: the stock is
#: physically at the vendor, and the only way to undo that is to receive it back.
JOB_WORK_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"APPROVED", "CANCELLED"}),
    "APPROVED": frozenset({"ISSUED", "CANCELLED"}),
    "ISSUED": frozenset({"PARTIALLY_RECEIVED", "RECEIVED"}),
    "PARTIALLY_RECEIVED": frozenset({"RECEIVED"}),
    "RECEIVED": frozenset({"CLOSED"}),
    "CLOSED": frozenset(),
    "CANCELLED": frozenset(),
}

#: Statuses in which a receipt may be posted.
RECEIVABLE_STATUSES = frozenset({"ISSUED", "PARTIALLY_RECEIVED"})
#: Statuses in which the header and the materials may still be edited.
EDITABLE_STATUSES = frozenset({"DRAFT"})

#: Mirrors `services/finance/app/domain.py::EWAY_THRESHOLD_MINOR` (INR 50,000, in paise) and
#: `app.dispatch.services._EWAY_VALUE_THRESHOLD_MINOR` — deliberately not imported (no
#: cross-service import, ADR-0000 §10). A pre-filter only: the real "is a bill required" call is
#: finance's, once a consumer for `operations.jobwork.challan.eway_requested` exists.
EWAY_VALUE_THRESHOLD_MINOR = 50_000_00


def can_transition(current: str, new: str) -> bool:
    """Whether a job-work order may move `current -> new`. A no-op "move" to the same status is
    not one — the same convention every other `can_transition_*` in this service documents."""
    return new in JOB_WORK_TRANSITIONS.get(current, frozenset())


def eway_required(total_value_minor: int, currency: str, *, inter_state: bool = False) -> bool:
    """Whether a challan needs an e-way bill. India-specific, so only INR is ever considered.

    GST rule applied (docs/inventory/jobwork.md is silent on it): goods sent to a job worker in
    another state need an e-way bill whatever their value (CGST Rule 138(1), job-work proviso);
    intra-state, the INR 50,000 threshold applies. The service holds no state data for the
    supplier or site, so the caller says whether the movement is inter-state."""
    if currency != "INR":
        return False
    return inter_state or total_value_minor > EWAY_VALUE_THRESHOLD_MINOR


IST = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")
#: How far ahead of today (IST) a receipt may be dated — covers a clock a day out and the
#: date line, not backdating the future.
RECEIVED_ON_FUTURE_TOLERANCE = timedelta(days=1)


def ist_today(now: datetime | None = None) -> date:
    """Today's date in India. Challan series and receipt dates are statutory Indian dates, not
    the server's local date."""
    return (now or datetime.now(UTC)).astimezone(IST).date()


def received_on_problem(received_on: date, today: date, issued_on: date) -> str | None:
    """Why a receipt date is unacceptable, or None. Not before the goods were sent, not in the
    future beyond tolerance."""
    if received_on > today + RECEIVED_ON_FUTURE_TOLERANCE:
        return f"received_on {received_on} is in the future"
    if received_on < issued_on:
        return f"received_on {received_on} is before the material was sent on {issued_on}"
    return None


def is_overdue(status: str, due_date: date | None, today: date) -> bool:
    """Overdue = the due date has passed while the material is still at the vendor."""
    return status in ("ISSUED", "PARTIALLY_RECEIVED") and due_date is not None and due_date < today


def days_overdue(status: str, due_date: date | None, today: date) -> int:
    if due_date is None or not is_overdue(status, due_date, today):
        return 0
    return (today - due_date).days


def financial_year(day: date) -> str:
    """The Indian financial year a date falls in, as the two-digit pair a challan series carries:
    1 Apr 2026 .. 31 Mar 2027 is `"26-27"`. A challan series restarts each financial year and
    must stay within 16 characters (CGST Rule 46/55), hence two digits, not four."""
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


@dataclass(frozen=True, slots=True)
class MaterialFigures:
    """One material's running figures, milli-units."""

    item_id: str
    sent: int
    received: int
    rejected: int
    shortage: int

    @property
    def accounted(self) -> int:
        return self.received + self.rejected + self.shortage

    @property
    def outstanding(self) -> int:
        return self.sent - self.accounted


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    #: Every material's outstanding is zero.
    documents_settled: bool
    #: The ledger's net `AT_JOB_WORKER` position for the order equals the documents', per item.
    ledger_consistent: bool
    #: Items whose ledger position disagrees with the documents: item id -> (documents, ledger).
    drift: dict[str, tuple[int, int]] = field(default_factory=dict)
    total_sent: int = 0
    total_received: int = 0
    total_rejected: int = 0
    total_shortage: int = 0
    total_outstanding: int = 0

    @property
    def balanced(self) -> bool:
        return self.documents_settled and self.ledger_consistent


def reconcile(
    materials: Sequence[MaterialFigures], ledger_at_vendor: Mapping[str, int]
) -> ReconciliationResult:
    """Compare an order's documents with the ledger. `ledger_at_vendor` is, per item id, the net
    of the `AT_JOB_WORKER` rows posted under this order — positive while stock is out. An item
    absent from it counts as zero, which is exactly what an order that was never issued has."""
    by_item: dict[str, int] = {}
    for m in materials:
        by_item[m.item_id] = by_item.get(m.item_id, 0) + m.outstanding
    drift: dict[str, tuple[int, int]] = {}
    for item_id in by_item.keys() | ledger_at_vendor.keys():
        documents = by_item.get(item_id, 0)
        ledger = ledger_at_vendor.get(item_id, 0)
        if documents != ledger:
            drift[item_id] = (documents, ledger)
    return ReconciliationResult(
        documents_settled=all(m.outstanding == 0 for m in materials),
        ledger_consistent=not drift,
        drift=drift,
        total_sent=sum(m.sent for m in materials),
        total_received=sum(m.received for m in materials),
        total_rejected=sum(m.rejected for m in materials),
        total_shortage=sum(m.shortage for m in materials),
        total_outstanding=sum(m.outstanding for m in materials),
    )
