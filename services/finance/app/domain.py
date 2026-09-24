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

Three further compliance vocabularies sit on top of that split, and each is here rather than in
`services` for the same reason — they are arithmetic somebody has to be able to check:

* **GSTR-1 assembly** (`assemble_gstr1`) turns a period's frozen documents into the sections the
  return declares. It **never computes tax**: every figure is a sum of the splits the invoices
  already carry, so the return foots to its own documents by construction. Each reported document
  lands in exactly one section, which is what makes that footing provable rather than hopeful.
* **e-Way validity** (`eway_valid_until`) — one day per 200 km or part thereof, each expiring at
  midnight of the day *following* generation. Computed from stored values so a screen and a
  roadside checkpoint read the same arithmetic.
* **Withholding** (`compute_gst_tds`, `compute_income_tax_withholding`) — GST s. 51 as a cliff on
  a document's whole taxable value, 194Q/206C(1H) as aggregates biting only on a financial year's
  excess. The s. 51 split reuses `split_gst` rather than re-halving, because a deduction must
  round exactly as the levy it sits beside does.

The block of *representative* figures at the bottom — the cash balance, the KPI spark-lines and
deltas, the six-month cashflow, and the trial-balance accounts — are not derivable from an AR
book alone (they need a cash ledger and a general ledger this slice does not own). They are
carried as constants that match the approved SPA fixtures so the Overview and Ledger screens
render identically live, and are the obvious seam a real GL integration replaces later.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------- vocabularies

INVOICE_STATUSES: frozenset[str] = frozenset({"draft", "sent", "paid", "overdue", "void"})
DOCUMENT_TYPES: frozenset[str] = frozenset({"invoice", "credit_note"})
PAYMENT_METHODS: frozenset[str] = frozenset({"bank_transfer", "upi", "card", "cheque", "cash"})

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
#: previously registered IRN was withdrawn (the IRP allows this within 24 hours); `cancelling` is
#: the **claim** for that withdrawal (see below).
#:
#: **Registration has no claim row; cancellation does** (D3, ADR-0015 addendum).
#: `stage_registration_attempt` writes nothing before the portal is asked — registering a document
#: is itself idempotent (the IRN is a pure function of the document, `IrpDuplicate` is a real
#: portal saying so), so a process that dies between the call and `record_registration_result`
#: leaves *no* row behind and a resumed attempt simply asks again. `app/cron.py`'s filing-reclaim
#: sweep therefore looks for the *absence* of a row for a registration.
#:
#: Cancelling an IRN is **not** idempotent by document: it is time-boxed (24 h from the
#: acknowledgement), needs a reason and an actor, and once done it is done. So the withdrawal is
#: claimed first — a `cancelling` row carrying the reason, the remark and the acknowledgement it
#: withdraws, audited as `einvoice.cancel_requested` — then the portal is called with no
#: transaction open, then the outcome is recorded as a `cancelled` (or `failed`) row. A
#: `cancelling` row that is still the newest for its invoice past `resume_after` is what the sweep
#: resumes; a portal that answers "already cancelled" is success-by-fetch (`IrpAlreadyCancelled`).
FILING_STATUSES: frozenset[str] = frozenset(
    {"pending", "filed", "failed", "cancelled", "cancelling"}
)

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

#: The two remedies for a document that was filed wrongly, and they are mutually exclusive in
#: time: `cancel` while the portal's window is open, `credit_note` once it has closed. Named as a
#: vocabulary because a screen must offer exactly one of them, and offering both is how a user
#: learns which is right by having the other fail.
#: `contact_support` is the one a person cannot resolve from a screen: a withdrawal the platform
#: asked for, never saw confirmed, and can no longer ask again because the window has shut — the
#: portal may or may not hold it, and only a look at the portal says which (D3).
FILING_REMEDIES: frozenset[str] = frozenset({"cancel", "credit_note", "contact_support"})


# --------------------------------------------------------------------- the retry policy
# Filing is automatic, so the retry policy is a rule rather than an operator's judgement, and it
# has to distinguish two failures that look alike and are opposite:
#
#   the portal refused the document  — final until the document changes. Retrying identical bytes
#                                      is how an integration goes quiet and a deadline passes.
#   the portal did not answer        — retryable with the identical document, and *must* be
#                                      retried, because the reporting window is days not weeks.
#
# Both are bounded. An unbounded retry on an outage is indistinguishable from an outage nobody
# noticed: the queue drains forever, the invoice is never filed, and no human is ever told.

#: How many times the platform files one document on its own before it stops and asks a person.
#: Five attempts across the backoff below spans a little over two hours — long enough to ride out
#: an ordinary portal outage, short enough that a genuinely stuck document surfaces the same day.
MAX_FILING_ATTEMPTS: int = 5

#: The first retry waits this long; each subsequent one doubles, to the ceiling.
FILING_RETRY_BASE: timedelta = timedelta(minutes=2)
FILING_RETRY_CEILING: timedelta = timedelta(hours=1)

#: How much clock skew the early-arrival guard forgives (`app/consumers.py:file_requested_invoice`,
#: ADR-0015 §5) before it treats a `filing_requested` message as arriving early. The scheduler
#: fires a job the instant its own discovery query sees `due_at <= now()` on the database's own
#: clock; this consumer's guard reads wall-clock time on whatever pod happens to run it. Neither
#: pod nor broker latency is expected to exceed this by much, and the guard exists for a message
#: arriving *meaningfully* early (a hand redelivery, a DLQ replay), not to reject one the ordinary
#: last few seconds of scheduling latency produced.
FILING_EARLY_ARRIVAL_TOLERANCE: timedelta = timedelta(seconds=30)

#: How long a reportable, unfiled invoice may sit with no filing row newer than this before the
#: filing-reclaim sweep (`app/cron.py`) treats the automatic pipeline as having lost track of it
#: — the `filing_requested` event never arrived, or a process died between calling the portal and
#: recording what it said, leaving no row behind at all (see `FILING_STATUSES`). Comfortably
#: longer than the finalize-to-first-attempt path and a single portal call are ever expected to
#: take together, so an invoice genuinely still in flight is never mistaken for lost.
FILING_RECLAIM_AFTER: timedelta = timedelta(minutes=5)

#: How many times the reclaim sweep will re-drive one stuck claim (a `cancelling` filing, a
#: `submitting` or `cancelling` e-Way row) before it stops and writes a terminal `failed` row
#: carrying `retry_exhausted`. The poisoned-step guard: a claim that keeps failing must become a
#: visible failure a person can act on, not a row the sweep picks up on every tick forever. Each
#: re-drive appends a fresh claim row, which doubles as its **lease** — see `resume_after`.
MAX_COMPLIANCE_RESUMES: int = MAX_FILING_ATTEMPTS


def resume_after(attempts: int) -> timedelta:
    """How long a stuck claim must have stood, with nothing newer for its document, before the
    reclaim sweep re-drives it — the bounded backoff between re-drives.

    `attempts` is how many drives the chain has had so far (1-based). The floor is
    `FILING_RECLAIM_AFTER` (comfortably longer than one portal call); each further drive adds the
    same capped exponential the automatic filing retry uses, so a portal that is down for an hour
    is not hammered by a sweep ticking every minute. Because every re-drive appends a fresh claim
    row, "newest row older than this" is also the **lease**: two sweep replicas, or a sweep and a
    request, cannot both pass it for the same document.
    """
    attempts = max(1, attempts)
    backoff: timedelta = min(FILING_RETRY_BASE * (2 ** (attempts - 1)), FILING_RETRY_CEILING)
    return FILING_RECLAIM_AFTER + backoff - FILING_RETRY_BASE


def pending_redrive_after(attempts: int) -> timedelta:
    """How long a `pending` row (an attempt that got an outage answer, with a retry owed) may
    stand with no successor before the sweep concludes the retry it scheduled was lost.

    The retry was staged for `filing_retry_delay(attempts)` after the row; the sweep waits that
    long **plus** `FILING_RECLAIM_AFTER` of grace. Finance cannot see the scheduler's own table
    (no cross-schema reads), so "no live scheduler job" is inferred from time alone — a deliberately
    generous bound, and a re-drive that turns out to be redundant is harmless because the
    consumer's claim asks `live_registration` first.
    """
    return filing_retry_delay(max(1, min(attempts, MAX_FILING_ATTEMPTS))) + FILING_RECLAIM_AFTER


#: Error codes this *platform* writes, as opposed to the numbered ones the portal returns. Kept
#: distinct from the portal's namespace (which is numeric) so "who said this" is never ambiguous
#: in a support conversation: `2227` came from NIC, `retry_exhausted` came from us.
ERR_PORTAL_UNAVAILABLE: str = "unavailable"
ERR_RETRY_EXHAUSTED: str = "retry_exhausted"
ERR_NO_REGISTRATION: str = "no_registration"
#: A withdrawal the reclaim sweep found still owed after the portal's own 24-hour window shut:
#: asking now can only be refused, so it stops and says so (D3).
ERR_WINDOW_CLOSED: str = "window_closed"
#: An e-Way generation chain older than `EWAY_REDRIVE_MAX_AGE`: goods that were meant to move a
#: day ago are not given a bill automatically now (D3).
ERR_STALE: str = "stale"

PLATFORM_ERROR_CODES: frozenset[str] = frozenset(
    {ERR_PORTAL_UNAVAILABLE, ERR_RETRY_EXHAUSTED, ERR_NO_REGISTRATION, ERR_WINDOW_CLOSED, ERR_STALE}
)


def may_retry_filing(attempt: int) -> bool:
    """Whether an outage on attempt `attempt` gets another try.

    `attempt` is 1-based: the first automatic attempt is 1. The comparison is `<` rather than
    `<=` because the question is "is there a *next* attempt", and off-by-one here is the
    difference between the documented budget and one more than it.
    """
    return attempt < MAX_FILING_ATTEMPTS


def filing_retry_delay(attempt: int) -> timedelta:
    """How long to wait before attempt `attempt + 1`. Exponential, capped.

    Capped rather than unbounded because the obligation has a deadline: a backoff that grows past
    the reporting window has stopped being patience and started being failure.
    """
    if attempt < 1:
        raise ValueError("filing attempts are 1-based")
    delay: timedelta = FILING_RETRY_BASE * (2 ** (attempt - 1))
    return min(delay, FILING_RETRY_CEILING)


def is_terminal_filing(status: str) -> bool:
    """Whether the platform has stopped trying and a person has to act.

    `failed` is the terminal state and the only one: `pending` is an attempt awaiting a retry,
    `filed` is done, `cancelled` is a withdrawal that leaves the document needing a new one.
    Reading this off the newest filing row is what turns "the automation gave up" from something
    an operator infers into something a screen can say.
    """
    return status == "failed"


def cancellation_deadline(ack_date: datetime) -> datetime:
    """The instant after which an IRN can no longer be withdrawn.

    Derived from the acknowledgement the portal returned and stored on the filing row, so the
    deadline a screen shows and the deadline the service enforces are the same arithmetic over
    the same timestamp — not two readings of a clock that can disagree.
    """
    return ack_date + IRN_CANCELLATION_WINDOW


def is_failed_withdrawal(status: str | None, ack_date: datetime | None) -> bool:
    """A `failed` row that carries an acknowledgement is a withdrawal that did not happen — the
    portal refused it, the window shut, or the reclaim sweep gave up — so **the registration it
    was withdrawing still stands**. A failed *registration* never carries one (D3)."""
    return status == "failed" and ack_date is not None


def remedy_for(*, status: str | None, ack_date: datetime | None, at: datetime) -> str | None:
    """Which correction is available for a document's current filing position at `at`.

    `cancel` only while a live registration is still inside the window; `credit_note` once it has
    closed. None when there is nothing to correct — an unfiled or already-withdrawn document has
    no registration standing against it.

    A failed withdrawal leaves the registration standing, so it is never "nothing to do": inside
    the window the answer is to ask again (`cancel`); outside it, the platform cannot know whether
    the portal holds a withdrawal it never confirmed, so a person has to look (`contact_support`).
    """
    if ack_date is None:
        return None
    if status == "filed":
        return "cancel" if cancellation_window_open(ack_date, at) else "credit_note"
    if is_failed_withdrawal(status, ack_date):
        return "cancel" if cancellation_window_open(ack_date, at) else "contact_support"
    return None


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


def parse_period(period: str) -> tuple[int, int]:
    """A GST return period, `YYYY-MM`, as a year and a month — or a `ValueError` saying why not.

    A return period is not a free-text label: it decides which documents a taxpayer is answering
    for. A malformed one that quietly resolved to "no documents" would look exactly like a clean
    period with nothing in it, which is the difference between "August is done" and "August was
    never fetched". Parsed here so a sync can refuse it before it writes anything.
    """
    parts = period.split("-")
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
        raise ValueError(f"{period!r} is not a return period (YYYY-MM)")
    try:
        year, month = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"{period!r} is not a return period (YYYY-MM)") from exc
    if not 1 <= month <= 12:
        raise ValueError(f"{period!r} names month {month}, which does not exist")
    return year, month


def next_period(period: str) -> str:
    """The calendar period immediately after `period` — December rolls into next January.

    Used to find the next *open* period for a posting that arrives after its own period's return
    has already been filed: `selectors.claim_period_for` finds it directly
    with no filed `gstr3b`, the same "amendment in a later period" shape a credit note against a
    filed invoice already has.
    """
    year, month = parse_period(period)
    if month == 12:
        return f"{year + 1}-01"
    return f"{year}-{month + 1:02d}"


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


#: Lifecycle states that are not a *reported* document. A draft has not been issued to anybody and
#: a void has been withdrawn; neither is a supply the tax authority should be told about, and
#: reporting one is not a harmless extra — an IRN is a hash of the document number and financial
#: year, so a draft filed by mistake burns the number the real document will need.
UNREPORTED_STATUSES: frozenset[str] = frozenset({"draft", "void"})


def should_auto_file(*, supply_type: str, document_type: str, status: str) -> bool:
    """Whether finalizing this document should queue a filing attempt of its own.

    Stricter than `requires_einvoice`, and deliberately so. That answers the statutory question —
    is this *kind* of supply reportable — while this one adds the lifecycle: an automatic filing
    must never register a draft or a void, because nobody asked it to and the mistake costs the
    document number.

    The manual route (`services.file_invoice`) is not held to this. Someone naming one document
    explicitly is making a decision; the automation is not, and the two deserve different defaults.
    """
    return status not in UNREPORTED_STATUSES and requires_einvoice(
        supply_type=supply_type, document_type=document_type
    )


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


# --------------------------------------------------------------------- IMS reconciliation
# The other side of the ledger. E-invoicing is what this business reports *outward*; the Invoice
# Management System is what its suppliers report *about* it — every document they file against
# this GSTIN lands in a queue, and what the recipient does with it decides how much input tax
# credit they may claim. Accepting an invoice for goods that never arrived is a credit claimed
# wrongly; rejecting a real one is a supplier relationship and a credit lost. Both are decisions,
# and both are made by a person.
#
# So this module suggests and never decides. Every function here returns a *suggestion carrying
# its reason*, and the reason is the point: "matched exactly" and "the supplier declares 500 paise
# more than the purchase order" ask for entirely different attention, and a queue that shows only
# an action leaves the operator to re-derive the reason for every row.

#: The document kinds a supplier can file against a recipient. The portal's own three.
IMS_DOCUMENT_TYPES: frozenset[str] = frozenset({"invoice", "credit_note", "debit_note"})

#: A document's position in the queue.
#:
#:   pending        — seen, not yet decided. Every document starts here.
#:   accepted       — the credit will be claimed.
#:   rejected       — refused; it goes back to the supplier to correct.
#:   pending_action — deliberately parked: real, but not this period's, or not yet agreed.
IMS_STATUSES: frozenset[str] = frozenset({"pending", "accepted", "rejected", "pending_action"})

#: What an operator may decide. One-to-one with the three settled statuses above; `pending` is not
#: a decision, it is the absence of one.
IMS_ACTIONS: frozenset[str] = frozenset({"accept", "reject", "pending_action"})

#: `action` → the status the document takes once that action is recorded.
IMS_ACTION_STATUS: dict[str, str] = {
    "accept": "accepted",
    "reject": "rejected",
    "pending_action": "pending_action",
}

# The reason classes. Deliberately few and deliberately disjoint: a reason an operator has to
# interpret is a reason that will be ignored.
#: The supplier's document and a recorded purchase agree, down to the paisa.
REASON_MATCHED: str = "matched"
#: Same document, different money. The delta is carried alongside, signed and in minor units.
REASON_AMOUNT_MISMATCH: str = "amount_mismatch"
#: Nothing has ever been bought from this GSTIN. The classic mis-addressed or fraudulent filing.
REASON_SUPPLIER_UNKNOWN: str = "supplier_unknown"
#: A known supplier, but no purchase document with this number. Usually a timing difference.
REASON_NO_PURCHASE_RECORD: str = "no_purchase_record"
#: The same supplier document has arrived twice. The later arrival is the duplicate.
REASON_DUPLICATE: str = "duplicate"

IMS_REASONS: frozenset[str] = frozenset(
    {
        REASON_MATCHED,
        REASON_AMOUNT_MISMATCH,
        REASON_SUPPLIER_UNKNOWN,
        REASON_NO_PURCHASE_RECORD,
        REASON_DUPLICATE,
    }
)


@dataclass(frozen=True, slots=True)
class InboundDocument:
    """One supplier document as it arrived from the portal — the facts a match is made on.

    `first_seen` and `source_ref` are load-bearing rather than decorative: when the same document
    arrives twice, they are what decide which arrival is the original and which is the duplicate.
    Without them the two are symmetrical and the answer depends on row order, which is to say on
    nothing — and "which of these is the duplicate" would come out differently on every run.

    `source_ref` breaks the tie because two copies fetched in the same sync share a `first_seen` to
    the microsecond. It is the portal's own reference and the portal's own ordering, which is a
    real statement about which it listed first; a row id would be a random number pretending to be
    one.
    """

    id: uuid.UUID
    source_ref: str
    supplier_gstin: str
    document_type: str
    document_number: str
    document_date: date
    taxable_minor: int
    total_minor: int
    first_seen: datetime


@dataclass(frozen=True, slots=True)
class PurchaseRecord:
    """One recorded purchase or expense document — the local counterpart a match is made against."""

    id: uuid.UUID
    supplier_gstin: str
    document_type: str
    document_number: str
    document_date: date
    taxable_minor: int
    total_minor: int


@dataclass(frozen=True, slots=True)
class Suggestion:
    """What the platform proposes for one inbound document, and why.

    Never applied on its own: `action` is what a screen pre-selects, `reason` and `detail` are what
    let the operator disagree with it in one glance. An auto-accept would be a credit claimed by a
    program, which is exactly the decision a person is accountable for.
    """

    action: str
    reason: str
    detail: str
    #: Signed, minor units: the supplier's total less the recorded document's. Positive means the
    #: supplier is claiming more than was recorded. Zero on every reason but `amount_mismatch`.
    delta_minor: int = 0
    matched_purchase_id: uuid.UUID | None = None
    duplicate_of_id: uuid.UUID | None = None


def document_key(
    *, supplier_gstin: str, document_type: str, document_number: str, on: date
) -> tuple[str, str, str, str]:
    """The identity a supplier document has under GST: whose, what kind, which number, which year.

    The financial year rather than the date, because that is the granularity the statute gives a
    document number: numbers restart each April and are unique within the year, so two documents
    numbered `INV-1` eleven months apart are the same document and two numbered `INV-1` in
    successive financial years are not. Matching on the exact date instead would call a supplier's
    corrected date a different document, which is the single most common false mismatch.
    """
    return (
        normalise_gstin(supplier_gstin),
        document_type,
        re.sub(r"\s", "", document_number).upper(),
        financial_year(on),
    )


def _key_of(doc: InboundDocument | PurchaseRecord) -> tuple[str, str, str, str]:
    return document_key(
        supplier_gstin=doc.supplier_gstin,
        document_type=doc.document_type,
        document_number=doc.document_number,
        on=doc.document_date,
    )


def _arrival(doc: InboundDocument) -> tuple[datetime, str]:
    """A total order over arrivals.

    The portal's reference breaks the tie, so two copies fetched in the same sync still have a
    stable original and a stable duplicate — and the same one every time the pass is re-run, which
    a random row id would not give.
    """
    return (doc.first_seen, doc.source_ref)


def suggest_action(
    document: InboundDocument,
    *,
    purchases: Sequence[PurchaseRecord],
    siblings: Sequence[InboundDocument],
) -> Suggestion:
    """The suggested action for one inbound document, with the reason it is suggested.

    `siblings` is every other inbound document for the same organization — the duplicate check
    needs them, and it runs **first**: a second copy of a document that already matched a purchase
    order still matches it, so a match-first ordering would suggest accepting the same credit
    twice. Duplicate beats match, always.

    Then, in the order the answers get less certain: an exactly matching purchase document, one
    matching by identity but not by money, a known supplier with nothing on file, and a supplier
    nothing has ever been bought from.
    """
    key = _key_of(document)

    original = min(
        (s for s in siblings if s.id != document.id and _key_of(s) == key),
        key=_arrival,
        default=None,
    )
    if original is not None and _arrival(original) < _arrival(document):
        return Suggestion(
            action="reject",
            reason=REASON_DUPLICATE,
            detail=(
                f"{document.document_number} from {document.supplier_gstin} has already been "
                "received; this is a second copy of the same document"
            ),
            duplicate_of_id=original.id,
        )

    from_supplier = [
        p
        for p in purchases
        if normalise_gstin(p.supplier_gstin) == normalise_gstin(document.supplier_gstin)
    ]
    if not from_supplier:
        return Suggestion(
            action="reject",
            reason=REASON_SUPPLIER_UNKNOWN,
            detail=(
                f"nothing has been purchased from {document.supplier_gstin}; no document from "
                "this supplier is on file"
            ),
        )

    same_document = [p for p in from_supplier if _key_of(p) == key]
    if not same_document:
        return Suggestion(
            action="pending_action",
            reason=REASON_NO_PURCHASE_RECORD,
            detail=(
                f"{document.supplier_gstin} is a known supplier, but no purchase document "
                f"numbered {document.document_number} is recorded for this financial year"
            ),
        )

    exact = next(
        (
            p
            for p in same_document
            if p.total_minor == document.total_minor and p.taxable_minor == document.taxable_minor
        ),
        None,
    )
    if exact is not None:
        return Suggestion(
            action="accept",
            reason=REASON_MATCHED,
            detail=(
                f"matches purchase document {exact.document_number} exactly, taxable value and "
                "total alike"
            ),
            matched_purchase_id=exact.id,
        )

    # Identity agrees, money does not. Pick the nearest recorded document so the delta the
    # operator is shown is the smallest one they have to explain.
    nearest = min(same_document, key=lambda p: abs(document.total_minor - p.total_minor))
    delta = document.total_minor - nearest.total_minor
    direction = "more" if delta > 0 else "less"
    return Suggestion(
        action="pending_action",
        reason=REASON_AMOUNT_MISMATCH,
        detail=(
            f"the supplier declares {abs(delta)} paise {direction} than purchase document "
            f"{nearest.document_number} ({document.total_minor} against {nearest.total_minor})"
        ),
        delta_minor=delta,
        matched_purchase_id=nearest.id,
    )


# --------------------------------------------------------------------- GSTR-1
# The outward-supplies return. Everything below assembles it and nothing below computes tax: the
# splits were decided once, per line, when the document was finalized, and a return that
# recomputed them would be a second implementation of the one calculation that must foot exactly
# — which is precisely how a return comes to disagree with the invoices it is made of.
#
# The invariant that makes the whole thing checkable: **every reported document lands in exactly
# one section, and every section's figures are sums of that document's stored line splits.** So
# the sections foot to the lines by construction, not by a reconciliation step, and a test can
# assert the equality directly rather than approximately.

#: The returns this service models. `gstr1` is outward supplies; `gstr7` is the deductor's
#: TDS return, which this slice reports the *figures* for without filing (see the README);
#: `gstr3b` is the summary return that nets outward liability against the ITC ledger — see
#: `assemble_gstr3b`, further down this module, beside the ledger it reads.
GST_RETURN_TYPES: frozenset[str] = frozenset({"gstr1", "gstr7", "gstr3b"})

#: A return's lifecycle. `draft` is a period nobody has assembled yet; `generated` carries a
#: snapshot of what would be reported; `filed` says the snapshot went to the portal. Forward
#: only — see `RETURN_TRANSITIONS`.
RETURN_STATUSES: frozenset[str] = frozenset({"draft", "generated", "filed"})

#: Which moves are legal. A filed return is terminal: the correction for a mistake in a filed
#: period is an amendment *in a later period*, which is GST's own model and the platform's — the
#: same shape as a credit note against a filed invoice.
RETURN_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"generated"}),
    "generated": frozenset({"generated", "filed"}),
    "filed": frozenset(),
}

#: The B2C split. An inter-state supply to an unregistered person is reported invoice by invoice
#: (B2CL) once its value passes this; everything else B2C is reported as a single aggregated
#: figure per place of supply and rate (B2CS). ₹2,50,000, in paise.
#:
#: **Strictly greater.** A ₹2,50,000 invoice is B2C-small; ₹2,50,000.01 is B2C-large. The
#: statute says "invoice value more than two and a half lakh rupees", and a boundary implemented
#: as `>=` misfiles every invoice that lands exactly on it — a class of error that is invisible
#: until a notice arrives, which is why it has a test at the exact rupee.
B2C_LARGE_THRESHOLD_MINOR: int = 250_000_00

#: The GSTR-1 sections this service assembles, in the order the return declares them.
GSTR1_SECTIONS: tuple[str, ...] = ("b2b", "b2cl", "b2cs", "cdnr", "cdnur", "exp", "hsn", "docs")


@dataclass(frozen=True, slots=True)
class ReturnLine:
    """One line of a document, as the return reads it — the stored split, never a recomputation."""

    hsn_sac: str | None
    description: str
    quantity: Decimal
    rate_pct: Decimal
    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int

    @property
    def split(self) -> TaxSplit:
        return TaxSplit(self.cgst_minor, self.sgst_minor, self.igst_minor)

    @property
    def total_minor(self) -> int:
        return self.taxable_minor + self.split.total_minor


@dataclass(frozen=True, slots=True)
class ReturnDocument:
    """One finalized document, reduced to what a return declares about it.

    A plain dataclass rather than an ORM row so the assembly below is pure and can be property-
    tested over generated books without a database. The selectors build these from `invoices`.
    """

    id: uuid.UUID
    number: str
    document_type: str
    issued_date: date
    supply_type: str
    place_of_supply: str | None
    reverse_charge: bool
    buyer_gstin: str | None
    buyer_name: str
    #: The document's own taxable value and total, as frozen. Carried so the assembly can assert
    #: the lines it was handed still foot to the header it was handed.
    taxable_minor: int
    total_minor: int
    status: str
    corrects_number: str | None
    lines: tuple[ReturnLine, ...]

    @property
    def split(self) -> TaxSplit:
        """The document's tax, summed from its lines — never read off the header."""
        total = ZERO_TAX
        for line in self.lines:
            total = total + line.split
        return total


@dataclass(frozen=True, slots=True)
class RateGroup:
    """One (rate, tax-head) group inside a section — the grain GSTR-1 is actually declared at."""

    rate_pct: Decimal
    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int

    @property
    def total_minor(self) -> int:
        return self.taxable_minor + self.cgst_minor + self.sgst_minor + self.igst_minor


@dataclass(frozen=True, slots=True)
class ReturnInvoiceEntry:
    """One document as B2B, B2CL, CDNR, CDNUR or EXP declares it: header plus rate groups."""

    document_id: uuid.UUID
    number: str
    document_type: str
    issued_date: date
    place_of_supply: str | None
    counterparty_gstin: str | None
    counterparty_name: str
    reverse_charge: bool
    invoice_value_minor: int
    corrects_number: str | None
    items: tuple[RateGroup, ...]

    @property
    def taxable_minor(self) -> int:
        return sum(item.taxable_minor for item in self.items)

    @property
    def split(self) -> TaxSplit:
        total = ZERO_TAX
        for item in self.items:
            total = total + TaxSplit(item.cgst_minor, item.sgst_minor, item.igst_minor)
        return total


@dataclass(frozen=True, slots=True)
class B2csEntry:
    """One aggregated B2C-small figure: place of supply x rate x whether it is inter-state.

    Aggregated because that is what the return declares — B2CS is not invoice-wise, it is a
    single number per (place of supply, rate). Credit notes to unregistered buyers below the
    B2CL threshold are netted in here with their sign, which is GST's own treatment and the
    reason the figure can legitimately be negative in a heavy-returns month.
    """

    place_of_supply: str | None
    rate_pct: Decimal
    inter_state: bool
    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    document_count: int

    @property
    def total_minor(self) -> int:
        return self.taxable_minor + self.cgst_minor + self.sgst_minor + self.igst_minor


@dataclass(frozen=True, slots=True)
class HsnEntry:
    """One row of the HSN summary: the code, the rate, and everything supplied under both."""

    hsn_sac: str | None
    rate_pct: Decimal
    quantity: Decimal
    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int

    @property
    def total_minor(self) -> int:
        return self.taxable_minor + self.cgst_minor + self.sgst_minor + self.igst_minor


@dataclass(frozen=True, slots=True)
class DocumentSeriesEntry:
    """One declared document series — the numbers issued, and how many were cancelled.

    GSTR-1 asks for the *range* of numbers a taxpayer used, not only the ones with tax on them,
    because a gap in a series is the first sign of an unreported invoice. Void documents are
    counted as cancelled rather than dropped, which is exactly why the range is asked for.
    """

    document_type: str
    from_number: str
    to_number: str
    total_count: int
    cancelled_count: int

    @property
    def net_count(self) -> int:
        return self.total_count - self.cancelled_count


@dataclass(frozen=True, slots=True)
class ReturnTotals:
    """What the return declares in one line. Every section sums to exactly this."""

    taxable_minor: int = 0
    cgst_minor: int = 0
    sgst_minor: int = 0
    igst_minor: int = 0
    document_count: int = 0
    line_count: int = 0

    @property
    def tax_minor(self) -> int:
        return self.cgst_minor + self.sgst_minor + self.igst_minor

    @property
    def total_minor(self) -> int:
        return self.taxable_minor + self.tax_minor


@dataclass(frozen=True, slots=True)
class Gstr1Return:
    """One period's outward supplies, assembled. Pure data — the snapshot a `gst_returns` row
    stores and the shape the read model serves back."""

    period: str
    gstin: str
    b2b: tuple[ReturnInvoiceEntry, ...]
    b2cl: tuple[ReturnInvoiceEntry, ...]
    b2cs: tuple[B2csEntry, ...]
    cdnr: tuple[ReturnInvoiceEntry, ...]
    cdnur: tuple[ReturnInvoiceEntry, ...]
    exp: tuple[ReturnInvoiceEntry, ...]
    hsn: tuple[HsnEntry, ...]
    docs: tuple[DocumentSeriesEntry, ...]
    totals: ReturnTotals


def in_period(on: date, period: str) -> bool:
    """Whether a document dated `on` belongs to return period `period` (`YYYY-MM`)."""
    year, month = parse_period(period)
    return on.year == year and on.month == month


def is_reportable_in_return(document_type: str, status: str) -> bool:
    """Whether a document's *figures* belong in a return.

    A draft was never issued, so it is not a supply. A void was withdrawn — it carries no tax,
    but its **number** is still declared in the document series, which is why `assemble_gstr1`
    keeps voids for that section and drops them from every other.
    """
    return document_type in DOCUMENT_TYPES and status not in UNREPORTED_STATUSES


def gstr1_section_for(document: ReturnDocument, *, seller_state_code: str = "") -> str:
    """The one section this document is declared in. Total, and mutually exclusive.

    Order matters and is the order the statute reads in:

    1. an **export** is EXP whoever the buyer is — it is inter-state by statute and carries no
       recipient GSTIN to file it against;
    2. a **credit note** goes to CDNR when the recipient is registered and CDNUR when they are
       not, because the two are reconciled by different parties;
    3. a supply to a **registered** recipient (including SEZ) is B2B;
    4. an unregistered recipient above the threshold, inter-state, is **B2CL**;
    5. everything else unregistered is **B2CS**.

    Exhaustive by construction: every branch returns, so a document cannot fall out of the
    return, which is the failure a reconciliation would only find months later.

    `seller_state_code` is needed for one case only — deciding whether a *nil-rated* B2C supply is
    inter-state, where there is no tax split to read the answer off. `assemble_gstr1` takes it
    from the GSTIN the return is being filed under.
    """
    if document.supply_type == "export":
        return "exp"
    if document.document_type == "credit_note":
        return "cdnr" if document.buyer_gstin else "cdnur"
    if document.buyer_gstin:
        return "b2b"
    return "b2cl" if _is_b2c_large(document, seller_state_code) else "b2cs"


def _is_b2c_large(document: ReturnDocument, seller_state_code: str) -> bool:
    """Inter-state, unregistered, and above ₹2.5 lakh — the three conditions, all required.

    "Above" is strict: an invoice landing exactly on the threshold is B2C-small.
    """
    if document.buyer_gstin:
        return False
    if not _is_inter_state(document, seller_state_code):
        return False
    return abs(document.total_minor) > B2C_LARGE_THRESHOLD_MINOR


def intra_state_from_split(
    split: TaxSplit, *, supply_type: str, seller_state_code: str, place_of_supply: str | None
) -> bool:
    """Whether a **frozen** document was taxed intra-state — read off its split, not re-derived.

    The distinction from `is_intra_state` matters, and it is why both exist. That one decides the
    heads a supply *will* be taxed under, at the moment it is raised. This one answers what a
    document *was* taxed under, months later, and the honest answer is the one written on it:
    re-deciding from the state codes would let a return — or a TDS deduction — disagree with the
    invoice it belongs to the moment the seller re-registers in another state.

    The fallback matters too. A **zero-rated or reverse-charge** document has no tax at all, and
    the two shapes are indistinguishable at zero — which is exactly the case B2C-large turns on,
    since a large nil-rated inter-state supply is still declared invoice-wise. So where there is
    nothing to read, it falls back to `is_intra_state`: the same rule that decided the heads when
    the document was raised, rather than a guess that quietly makes every nil-rated supply
    intra-state.

    One function rather than one per caller, because a second copy of this three-branch rule is a
    second copy that drifts.
    """
    if split.igst_minor:
        return False
    if split.cgst_minor or split.sgst_minor:
        return True
    return is_intra_state(
        supply_type=supply_type,
        seller_state_code=seller_state_code,
        place_of_supply=place_of_supply or "",
    )


def _is_inter_state(document: ReturnDocument, seller_state_code: str) -> bool:
    return not intra_state_from_split(
        document.split,
        supply_type=document.supply_type,
        seller_state_code=seller_state_code,
        place_of_supply=document.place_of_supply,
    )


def _rate_groups(lines: Sequence[ReturnLine]) -> tuple[RateGroup, ...]:
    """Collapse a document's lines onto the (rate) grain the return declares, summing splits."""
    buckets: dict[Decimal, list[int]] = {}
    for line in lines:
        bucket = buckets.setdefault(line.rate_pct, [0, 0, 0, 0])
        bucket[0] += line.taxable_minor
        bucket[1] += line.cgst_minor
        bucket[2] += line.sgst_minor
        bucket[3] += line.igst_minor
    return tuple(
        RateGroup(
            rate_pct=rate,
            taxable_minor=values[0],
            cgst_minor=values[1],
            sgst_minor=values[2],
            igst_minor=values[3],
        )
        for rate, values in sorted(buckets.items())
    )


def _entry_for(document: ReturnDocument) -> ReturnInvoiceEntry:
    return ReturnInvoiceEntry(
        document_id=document.id,
        number=document.number,
        document_type=document.document_type,
        issued_date=document.issued_date,
        place_of_supply=document.place_of_supply,
        counterparty_gstin=document.buyer_gstin,
        counterparty_name=document.buyer_name,
        reverse_charge=document.reverse_charge,
        invoice_value_minor=document.total_minor,
        corrects_number=document.corrects_number,
        items=_rate_groups(document.lines),
    )


def _b2cs_entries(
    documents: Sequence[ReturnDocument], seller_state_code: str
) -> tuple[B2csEntry, ...]:
    """Aggregate the B2C-small supplies by place of supply, rate and inter/intra-state.

    Aggregation is the section's whole shape: a retailer's thousand counter sales are one number
    per rate, not a thousand rows. The counts are carried alongside so a diff can say *how many*
    documents moved a figure, which is the first question anybody asks of an aggregate.
    """
    buckets: dict[tuple[str | None, Decimal, bool], list[int]] = {}
    seen: dict[tuple[str | None, Decimal, bool], set[uuid.UUID]] = {}
    for document in documents:
        inter = _is_inter_state(document, seller_state_code)
        for line in document.lines:
            key = (document.place_of_supply, line.rate_pct, inter)
            bucket = buckets.setdefault(key, [0, 0, 0, 0])
            bucket[0] += line.taxable_minor
            bucket[1] += line.cgst_minor
            bucket[2] += line.sgst_minor
            bucket[3] += line.igst_minor
            seen.setdefault(key, set()).add(document.id)
    return tuple(
        B2csEntry(
            place_of_supply=pos,
            rate_pct=rate,
            inter_state=inter,
            taxable_minor=values[0],
            cgst_minor=values[1],
            sgst_minor=values[2],
            igst_minor=values[3],
            document_count=len(seen[(pos, rate, inter)]),
        )
        for (pos, rate, inter), values in sorted(
            buckets.items(), key=lambda kv: (kv[0][0] or "", kv[0][1], kv[0][2])
        )
    )


def _hsn_summary(documents: Sequence[ReturnDocument]) -> tuple[HsnEntry, ...]:
    """Every line of every reported document, grouped by HSN/SAC and rate.

    Deliberately assembled from the *same* lines the sections are, rather than from the section
    entries: the HSN summary and the taxable-supply sections are two independent declarations of
    one book, and the portal cross-foots them. Building the second from the first would make them
    agree by construction and hide exactly the disagreement the cross-foot exists to catch — so
    they are built in parallel from the lines, and a test asserts they agree.

    A line with no HSN groups under `None` rather than being dropped: a missing code is a gap the
    return must show, not one it should quietly close.
    """
    buckets: dict[tuple[str | None, Decimal], list[int]] = {}
    quantities: dict[tuple[str | None, Decimal], Decimal] = {}
    for document in documents:
        for line in document.lines:
            key = (line.hsn_sac, line.rate_pct)
            bucket = buckets.setdefault(key, [0, 0, 0, 0])
            bucket[0] += line.taxable_minor
            bucket[1] += line.cgst_minor
            bucket[2] += line.sgst_minor
            bucket[3] += line.igst_minor
            quantities[key] = quantities.get(key, Decimal(0)) + line.quantity
    return tuple(
        HsnEntry(
            hsn_sac=hsn,
            rate_pct=rate,
            quantity=quantities[(hsn, rate)],
            taxable_minor=values[0],
            cgst_minor=values[1],
            sgst_minor=values[2],
            igst_minor=values[3],
        )
        for (hsn, rate), values in sorted(
            buckets.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])
        )
    )


def _document_series(documents: Sequence[ReturnDocument]) -> tuple[DocumentSeriesEntry, ...]:
    """The number ranges issued in the period, per document type, with the voids counted.

    This is the one section a *void* belongs in: its figures are not a supply, but its number was
    consumed, and a series with a hole in it is how an unreported invoice announces itself.
    """
    by_type: dict[str, list[str]] = {}
    cancelled: dict[str, int] = {}
    for document in documents:
        by_type.setdefault(document.document_type, []).append(document.number)
        if document.status == "void":
            cancelled[document.document_type] = cancelled.get(document.document_type, 0) + 1
    return tuple(
        DocumentSeriesEntry(
            document_type=document_type,
            from_number=min(numbers),
            to_number=max(numbers),
            total_count=len(numbers),
            cancelled_count=cancelled.get(document_type, 0),
        )
        for document_type, numbers in sorted(by_type.items())
    )


def _totals_of(documents: Sequence[ReturnDocument]) -> ReturnTotals:
    """The return's one-line declaration, summed straight off the lines.

    Summed from the *lines*, not from the assembled sections, for the same reason the HSN summary
    is: two independent paths to one number, so a test comparing them is a real check rather than
    a tautology.
    """
    taxable = 0
    split = ZERO_TAX
    line_count = 0
    for document in documents:
        for line in document.lines:
            taxable += line.taxable_minor
            split = split + line.split
            line_count += 1
    return ReturnTotals(
        taxable_minor=taxable,
        cgst_minor=split.cgst_minor,
        sgst_minor=split.sgst_minor,
        igst_minor=split.igst_minor,
        document_count=len(documents),
        line_count=line_count,
    )


def assemble_gstr1(documents: Sequence[ReturnDocument], *, period: str, gstin: str) -> Gstr1Return:
    """Assemble one period's outward-supplies return. Pure, and the only place sections are built.

    `documents` is every finalized document the organization issued in the period, voids and all.
    The voids are dropped from every section except the document series, where their numbers are
    declared and counted as cancelled.

    Nothing here computes tax. Every figure is a sum of stored line splits, so the return foots
    to the invoices it is made of by construction — which is the property the tests assert three
    ways: sections against lines, HSN summary against lines, and the one-line totals against
    both.

    The seller's state comes off the GSTIN this return is being filed under — the first two
    characters, by construction — rather than being passed separately, so the two can never
    disagree. It is used for exactly one decision: whether a nil-rated B2C supply is inter-state,
    where there is no tax split to read the answer off.
    """
    parse_period(period)  # refuse a malformed period before anything is assembled
    seller_state = gstin[:2]
    reported = [d for d in documents if is_reportable_in_return(d.document_type, d.status)]

    sections: dict[str, list[ReturnDocument]] = {
        name: [] for name in ("b2b", "b2cl", "cdnr", "cdnur", "exp", "b2cs")
    }
    for document in reported:
        sections[gstr1_section_for(document, seller_state_code=seller_state)].append(document)

    return Gstr1Return(
        period=period,
        gstin=gstin,
        b2b=tuple(_entry_for(d) for d in sections["b2b"]),
        b2cl=tuple(_entry_for(d) for d in sections["b2cl"]),
        b2cs=_b2cs_entries(sections["b2cs"], seller_state),
        cdnr=tuple(_entry_for(d) for d in sections["cdnr"]),
        cdnur=tuple(_entry_for(d) for d in sections["cdnur"]),
        exp=tuple(_entry_for(d) for d in sections["exp"]),
        hsn=_hsn_summary(reported),
        docs=_document_series(list(documents)),
        totals=_totals_of(reported),
    )


def section_totals(section: str, ret: Gstr1Return) -> ReturnTotals:
    """One section's own footing, so a diff and a test can talk about sections individually."""
    if section == "b2cs":
        rows = ret.b2cs
        return ReturnTotals(
            taxable_minor=sum(r.taxable_minor for r in rows),
            cgst_minor=sum(r.cgst_minor for r in rows),
            sgst_minor=sum(r.sgst_minor for r in rows),
            igst_minor=sum(r.igst_minor for r in rows),
            document_count=sum(r.document_count for r in rows),
            line_count=len(rows),
        )
    if section == "hsn":
        return ReturnTotals(
            taxable_minor=sum(r.taxable_minor for r in ret.hsn),
            cgst_minor=sum(r.cgst_minor for r in ret.hsn),
            sgst_minor=sum(r.sgst_minor for r in ret.hsn),
            igst_minor=sum(r.igst_minor for r in ret.hsn),
            document_count=0,
            line_count=len(ret.hsn),
        )
    entries: tuple[ReturnInvoiceEntry, ...] = getattr(ret, section)
    split = ZERO_TAX
    for entry in entries:
        split = split + entry.split
    return ReturnTotals(
        taxable_minor=sum(e.taxable_minor for e in entries),
        cgst_minor=split.cgst_minor,
        sgst_minor=split.sgst_minor,
        igst_minor=split.igst_minor,
        document_count=len(entries),
        line_count=sum(len(e.items) for e in entries),
    )


def totals_delta(before: ReturnTotals, after: ReturnTotals) -> ReturnTotals:
    """`after - before`, component-wise — the shape a diff between two snapshots reports.

    Signed on purpose. "The book moved by -4,200 paise" is an answer; "the book differs" is not.
    """
    return ReturnTotals(
        taxable_minor=after.taxable_minor - before.taxable_minor,
        cgst_minor=after.cgst_minor - before.cgst_minor,
        sgst_minor=after.sgst_minor - before.sgst_minor,
        igst_minor=after.igst_minor - before.igst_minor,
        document_count=after.document_count - before.document_count,
        line_count=after.line_count - before.line_count,
    )


def may_transition_return(current: str, wanted: str) -> bool:
    """Whether a return may move from `current` to `wanted`. Forward only; `filed` is terminal."""
    return wanted in RETURN_TRANSITIONS.get(current, frozenset())


# --------------------------------------------------------------------- the input-tax-credit ledger
# GSTR-3B's other half, and a ledger in its own right. Three kinds of row, all append-only —
#
#   accrual      — eligible input tax credit from an IMS document this organization *accepted*.
#   reversal     — an accepted document's credit withdrawn, because the decision changed.
#   utilisation  — credit consumed against output liability when a GSTR-3B is filed.
#
# and the balance is never a column: it is `SUM(amount_minor)` per (registration, head, period),
# carried forward the way every append-only ledger on this platform is read — the same shape as
# the credit-pack draws commerce settled on. `amount_minor` is signed: positive on an accrual,
# negative on a reversal or a utilisation, so the sum is the balance without a case split.
#
# **Section 17(5) blocked credit — what is handled and what is not.** The inbound document model
# (`ims_documents`) carries no HSN, no description and no line items — only a supplier, a document
# and four money columns — so there is nothing in the data to *derive* a blocked-credit category
# from. What this slice does instead is let the person accepting a document say what it was for,
# at the moment they accept it (`ImsDecisionCreate.expense_category`), and refuse to accrue credit
# on the categories the statute blocks:
#
#   motor_vehicle              s. 17(5)(a)/(aa)  — motor vehicles for transport of persons (seating
#                              ≤ 13) and vessels/aircraft, outside the further-supply/training/
#                              transport-of-goods carve-outs this slice does not distinguish.
#   food_beverage              s. 17(5)(b)(i)     — food, beverages, outdoor catering, and the
#                              health/life insurance this slice folds into the same bucket rather
#                              than adding a fifth category for.
#   membership_club_fitness    s. 17(5)(b)(ii)    — membership of a club, health or fitness centre.
#   works_contract_immovable   s. 17(5)(c)/(d)    — works contract services and goods/services for
#                              construction of immovable property on one's own account, other than
#                              plant and machinery.
#   personal_consumption       s. 17(5)(g)        — goods or services for personal consumption.
#
# Deliberately **not** modelled: s. 17(5)(h) (goods lost, stolen, destroyed, written off, or given
# as gifts or free samples) and s. 17(5)(i) (tax paid under composition) — both are facts about
# what happened to goods *after* receipt, which nothing on `purchase_documents` or `ims_documents`
# tracks, and inventing a column to hold a fact this platform never observes would be worse than
# leaving the gap visible. A document in either situation is accepted as `general` today, which
# overstates eligible credit until that tracking exists — called out again in the docstring of
# `is_itc_eligible` so it is not a fact somebody has to already know to find.
ITC_TAX_HEADS: frozenset[str] = frozenset({"igst", "cgst", "sgst", "cess"})

#: The same four heads, ordered — for a posting loop or a report column order, where a set's
#: undefined iteration order would make two runs list a document's postings differently.
ITC_TAX_HEAD_ORDER: tuple[str, ...] = ("igst", "cgst", "sgst", "cess")

#: The categories `ImsDecisionCreate.expense_category` accepts. `general` is the default and the
#: only one this slice can actually observe most purchases as, since nothing upstream classifies
#: an inbound document any further.
ITC_EXPENSE_CATEGORIES: frozenset[str] = frozenset(
    {
        "general",
        "motor_vehicle",
        "food_beverage",
        "membership_club_fitness",
        "works_contract_immovable",
        "personal_consumption",
    }
)

#: The categories s. 17(5) blocks outright. See the module note above for the statutory mapping
#: and for what is deliberately absent from this set.
BLOCKED_ITC_CATEGORIES: frozenset[str] = frozenset(
    {
        "motor_vehicle",
        "food_beverage",
        "membership_club_fitness",
        "works_contract_immovable",
        "personal_consumption",
    }
)

#: What a ledger row records itself as having come from — `itc_ledger_entries.source_type`.
ITC_SOURCE_TYPES: frozenset[str] = frozenset({"ims_decision", "gst_return"})

#: A posted row's kind — `itc_ledger_entries.kind`.
ITC_POSTING_KINDS: frozenset[str] = frozenset({"accrual", "reversal", "utilisation"})


def is_itc_eligible(category: str) -> bool:
    """Whether a purchase in this category may accrue input tax credit at all.

    `cess` is not a real observed head today — see `TaxHeadAmounts` — but the categories above
    block a purchase's *whole* claim regardless of head, which is the statute's own shape: s.
    17(5) does not partially block a document, it blocks the claim on it entirely.
    """
    return category not in BLOCKED_ITC_CATEGORIES


@dataclass(frozen=True, slots=True)
class TaxHeadAmounts:
    """An amount broken across the four heads GST is payable under.

    `cess_minor` is carried for forward compatibility and is always zero on every path this
    slice produces: no table anywhere in this service — not `invoices`, not `purchase_documents`,
    not `ims_documents` — has a cess column, because `split_gst` itself only ever computes
    CGST/SGST/IGST. Adding cess for real is a change to that split and to every table it feeds,
    which is a wider change than an ITC ledger; the field exists here so the ledger's schema does
    not need to change shape on the day that lands.
    """

    igst_minor: int = 0
    cgst_minor: int = 0
    sgst_minor: int = 0
    cess_minor: int = 0

    @property
    def total_minor(self) -> int:
        return self.igst_minor + self.cgst_minor + self.sgst_minor + self.cess_minor

    def __add__(self, other: TaxHeadAmounts) -> TaxHeadAmounts:
        return TaxHeadAmounts(
            igst_minor=self.igst_minor + other.igst_minor,
            cgst_minor=self.cgst_minor + other.cgst_minor,
            sgst_minor=self.sgst_minor + other.sgst_minor,
            cess_minor=self.cess_minor + other.cess_minor,
        )

    def __sub__(self, other: TaxHeadAmounts) -> TaxHeadAmounts:
        return TaxHeadAmounts(
            igst_minor=self.igst_minor - other.igst_minor,
            cgst_minor=self.cgst_minor - other.cgst_minor,
            sgst_minor=self.sgst_minor - other.sgst_minor,
            cess_minor=self.cess_minor - other.cess_minor,
        )

    def for_head(self, head: str) -> int:
        return getattr(self, f"{head}_minor")  # type: ignore[no-any-return]


ZERO_ITC = TaxHeadAmounts()


def itc_eligible_amounts(document: TaxHeadAmounts, *, category: str) -> TaxHeadAmounts:
    """The credit a document's own tax split would accrue, or nothing at all when blocked.

    Not a partial reduction — `is_itc_eligible` is a whole-document gate, so a blocked category
    is `ZERO_ITC` across every head rather than a head-by-head means test the statute does not
    describe.
    """
    return document if is_itc_eligible(category) else ZERO_ITC


def split_positive_negative(amounts: TaxHeadAmounts) -> tuple[TaxHeadAmounts, TaxHeadAmounts]:
    """Split a signed amount into its non-negative part and its non-positive part, head by head —
    `positive.for_head(h) + negative.for_head(h) == amounts.for_head(h)` for every head.

    What `services.record_ims_decision` uses to turn one delta on the ledger (what should now be
    outstanding, minus what already is) into the two kinds of row the ledger actually holds: the
    positive part posts as an `accrual`, the negative part as a `reversal`. One decision can
    legitimately produce both in general, even though in practice a document is taxed under a
    single shape (IGST alone, or CGST+SGST together) so a real delta is never mixed-sign across
    its own two or three live heads.
    """
    positive = TaxHeadAmounts(
        igst_minor=max(0, amounts.igst_minor),
        cgst_minor=max(0, amounts.cgst_minor),
        sgst_minor=max(0, amounts.sgst_minor),
        cess_minor=max(0, amounts.cess_minor),
    )
    negative = TaxHeadAmounts(
        igst_minor=min(0, amounts.igst_minor),
        cgst_minor=min(0, amounts.cgst_minor),
        sgst_minor=min(0, amounts.sgst_minor),
        cess_minor=min(0, amounts.cess_minor),
    )
    return positive, negative


@dataclass(frozen=True, slots=True)
class UtilisationLine:
    """One application of credit against liability — one credit head, against one liability head,
    for one amount. `head == against` is the ordinary case; the three cross-heads the statute
    permits (`igst`→`cgst`, `igst`→`sgst`, `cgst`→`igst`, `sgst`→`igst`) are the exceptional one,
    and `cgst`↔`sgst` never appears at all."""

    head: str
    against: str
    amount_minor: int


@dataclass(frozen=True, slots=True)
class UtilisationResult:
    """What filing a GSTR-3B does to the credit ledger and the liability: every application made,
    what is left of the credit afterwards (carried forward), what liability nothing covered
    (payable in cash), and what liability was negative to begin with (`excess_liability`)."""

    lines: tuple[UtilisationLine, ...]
    credit_remaining: TaxHeadAmounts
    cash_payable: TaxHeadAmounts
    #: The magnitude of any *negative* liability handed in — a heavy-credit-note period reporting
    #: more corrected than supplied at a head. Always `>= 0` per head. Never treated as spendable
    #: credit and never netted into `cash_payable`; carried here as a visible, reported excess
    #: because GSTR-3B's own refund/carry-forward workflow for it is out of scope for this slice —
    #: see the module note on `utilise_itc`.
    excess_liability: TaxHeadAmounts
    #: The magnitude of any *negative* available credit handed in — a credit note's own negative
    #: accrual (see `services.record_ims_decision`) outrunning what a head had otherwise accrued.
    #: Always `>= 0` per head. Never treated as a liability and never added to `cash_payable`;
    #: carried here, visibly, for the same reason `excess_liability` is — dropping it silently
    #: would understate how deep a head has gone negative rather than merely reporting it as zero.
    credit_deficit: TaxHeadAmounts


def _apply(credit: int, liability: int) -> tuple[int, int, int]:
    """Use as much of `credit` against `liability` as either allows. Returns
    `(amount_applied, credit_left, liability_left)` — all non-negative given non-negative inputs,
    since `min` of two non-negative numbers is itself non-negative and never exceeds either."""
    applied = min(credit, liability)
    return applied, credit - applied, liability - applied


def _clamp_non_negative(amounts: TaxHeadAmounts) -> TaxHeadAmounts:
    return TaxHeadAmounts(
        igst_minor=max(0, amounts.igst_minor),
        cgst_minor=max(0, amounts.cgst_minor),
        sgst_minor=max(0, amounts.sgst_minor),
        cess_minor=max(0, amounts.cess_minor),
    )


def utilise_itc(available: TaxHeadAmounts, liability: TaxHeadAmounts) -> UtilisationResult:
    """The statutory utilisation order (CGST Rule 88A), as pure integer arithmetic, minimising
    cash payable subject to it.

    **Negative inputs are real and are handled before any of the rest of this runs.** A period
    heavy with credit notes can report negative liability at a head (more corrected than
    supplied); this platform's ledger can likewise report negative available credit at a head once
    a credit note's own negative accrual (see `services.record_ims_decision`) outruns what was
    otherwise accrued. Neither is spendable: a negative liability is not a debt owed by the
    government, and a negative credit balance is not a credit this function can hand out. Both are
    clamped to zero before the statute's own arithmetic runs; the magnitude of a clamped negative
    liability is returned separately as `excess_liability` rather than silently disappearing, so
    it stays visible even though carrying it forward into a refund claim or a future period's
    Table 3.1 is real functionality this slice does not implement.

    Four rules, applied in this order because each is a precondition of the ones after it:

    1. **IGST credit against its own liability first.**
    2. **The rest of IGST credit is routed to minimise cash, not merely to satisfy the ordering.**
       CGST and SGST liability that *their own* native credit could never reach anyway — the
       shortfall `max(0, liability - own credit)` — is paid from IGST before native credit gets a
       chance, so native CGST/SGST credit is never left idle while IGST pays a liability the
       credit sitting right beside it could have paid instead. (The defect this replaces: credit
       igst=100 cgst=100 sgst=0 against liability cgst=100 sgst=100 used to leave 100 in cash with
       100 of CGST credit stranded; paying the SGST shortfall from IGST and letting native CGST
       credit cover CGST leaves nothing in cash.) Any IGST still left after both shortfalls is
       offered to whatever of CGST/SGST liability remains, because Rule 88A requires IGST credit
       be **completely** exhausted before CGST or SGST credit may be used at all — not merely used
       "enough".
    3. **CGST credit** then goes against CGST liability, and only the remainder against IGST.
       It never touches SGST liability — CGST and SGST are separate governments' money and the
       statute keeps them apart even when both are owed to the same taxpayer.
    4. **SGST credit** mirrors it: SGST liability first, the remainder against IGST, never CGST.

    Cess stands alone throughout: cess credit offsets only cess liability, because no other head's
    credit may touch it and it may not touch any other head.

    Every step is a `min(...)`, so the result is non-negative by construction, no step ever
    manufactures negative credit or negative liability, and `credit_remaining + applied` and
    `cash_payable + applied` each foot back to the (clamped) inputs — asserted as invariants over
    generated inputs, including negative ones, in `tests/test_itc.py`, alongside a brute-force
    check that cash payable really is the minimum Rule 88A allows and a stronger ordering check:
    whenever any CGST or SGST credit is applied to anything, IGST credit is already fully spent.
    """
    excess_liability = TaxHeadAmounts(
        igst_minor=max(0, -liability.igst_minor),
        cgst_minor=max(0, -liability.cgst_minor),
        sgst_minor=max(0, -liability.sgst_minor),
        cess_minor=max(0, -liability.cess_minor),
    )
    credit_deficit = TaxHeadAmounts(
        igst_minor=max(0, -available.igst_minor),
        cgst_minor=max(0, -available.cgst_minor),
        sgst_minor=max(0, -available.sgst_minor),
        cess_minor=max(0, -available.cess_minor),
    )
    liability = _clamp_non_negative(liability)
    available = _clamp_non_negative(available)

    lines: list[UtilisationLine] = []

    def use(credit: int, liability: int, head: str, against: str) -> tuple[int, int]:
        applied, credit_left, liability_left = _apply(credit, liability)
        if applied:
            lines.append(UtilisationLine(head=head, against=against, amount_minor=applied))
        return credit_left, liability_left

    def use_at_most(
        credit: int, liability: int, cap: int, head: str, against: str
    ) -> tuple[int, int]:
        """Apply at most `cap` of `credit` against `liability` — the shortfall-targeted step,
        where the amount offered is bounded by neither the liability nor the credit alone but by
        how much of the liability the *other* credit at that head cannot reach anyway."""
        applied = min(credit, liability, cap)
        if applied:
            lines.append(UtilisationLine(head=head, against=against, amount_minor=applied))
        return credit - applied, liability - applied

    igst_credit, igst_liab = available.igst_minor, liability.igst_minor
    cgst_credit, cgst_liab = available.cgst_minor, liability.cgst_minor
    sgst_credit, sgst_liab = available.sgst_minor, liability.sgst_minor
    cess_credit, cess_liab = available.cess_minor, liability.cess_minor

    # 1. IGST credit against its own liability first.
    igst_credit, igst_liab = use(igst_credit, igst_liab, "igst", "igst")

    # 2. Route the rest of IGST credit to minimise cash: pay the shortfall native CGST/SGST credit
    # cannot reach first, then (Rule 88A requiring IGST be *fully* exhausted first) whatever of
    # CGST/SGST liability still remains.
    shortfall_cgst = max(0, cgst_liab - cgst_credit)
    shortfall_sgst = max(0, sgst_liab - sgst_credit)
    igst_credit, cgst_liab = use_at_most(igst_credit, cgst_liab, shortfall_cgst, "igst", "cgst")
    igst_credit, sgst_liab = use_at_most(igst_credit, sgst_liab, shortfall_sgst, "igst", "sgst")
    igst_credit, cgst_liab = use(igst_credit, cgst_liab, "igst", "cgst")
    igst_credit, sgst_liab = use(igst_credit, sgst_liab, "igst", "sgst")

    # 3. CGST credit: CGST liability first, remainder against IGST. Never SGST.
    cgst_credit, cgst_liab = use(cgst_credit, cgst_liab, "cgst", "cgst")
    cgst_credit, igst_liab = use(cgst_credit, igst_liab, "cgst", "igst")

    # 4. SGST credit: SGST liability first, remainder against IGST. Never CGST.
    sgst_credit, sgst_liab = use(sgst_credit, sgst_liab, "sgst", "sgst")
    sgst_credit, igst_liab = use(sgst_credit, igst_liab, "sgst", "igst")

    # Cess: only ever against cess.
    cess_credit, cess_liab = use(cess_credit, cess_liab, "cess", "cess")

    # IGST's shortfall-targeted step and its generic "whatever remains" step can both post against
    # the same (head, against) pair — merge them into one line so a report reads "IGST -> CGST
    # 9,000" rather than two rows that happen to sum to it.
    merged: dict[tuple[str, str], int] = {}
    order: list[tuple[str, str]] = []
    for line in lines:
        key = (line.head, line.against)
        if key not in merged:
            order.append(key)
        merged[key] = merged.get(key, 0) + line.amount_minor
    lines = [UtilisationLine(head=h, against=a, amount_minor=merged[(h, a)]) for h, a in order]

    return UtilisationResult(
        lines=tuple(lines),
        credit_remaining=TaxHeadAmounts(
            igst_minor=igst_credit,
            cgst_minor=cgst_credit,
            sgst_minor=sgst_credit,
            cess_minor=cess_credit,
        ),
        cash_payable=TaxHeadAmounts(
            igst_minor=igst_liab, cgst_minor=cgst_liab, sgst_minor=sgst_liab, cess_minor=cess_liab
        ),
        excess_liability=excess_liability,
        credit_deficit=credit_deficit,
    )


@dataclass(frozen=True, slots=True)
class Gstr3bOutward:
    """Table 3.1 — outward supplies and inward supplies liable to reverse charge.

    Only (a) and (b) are ever non-zero on this platform today, and the docstring on each field
    says why: (c), (d) and (e) all ask for a fact no table here records.
    """

    #: 3.1(a) — taxable supplies other than zero-rated: every reportable `b2b`/`b2c` document that
    #: is not itself marked reverse-charge. A document this organization raised under reverse
    #: charge is excluded here and carried separately in `outward_reverse_charge` — the recipient,
    #: not this organization, owes the tax on it, and folding it into (a) would make Table 6.1
    #: collect cash from the wrong side of the supply.
    taxable_supplies: TaxHeadAmounts
    outward_taxable_minor: int
    #: 3.1(b) — zero-rated supplies: `export` and `sez`, inter-state by statute regardless of the
    #: rate actually charged.
    zero_rated: TaxHeadAmounts
    zero_rated_taxable_minor: int
    #: 3.1(c) — nil-rated and exempt supplies. Always zero: no document here carries a flag
    #: distinguishing an exempt supply from a taxable one billed at a 0% band: both look like "no
    #: tax", and only one of them is what this section asks for.
    nil_rated_taxable_minor: int
    #: 3.1(d) — inward supplies liable to reverse charge. Always zero: `purchase_documents` and
    #: `ims_documents` carry no reverse-charge flag; `invoices.reverse_charge` records the
    #: *outward* side of a reverse-charge supply this organization raised, which is a different
    #: fact from what this organization owes as the recipient of somebody else's.
    reverse_charge_inward_taxable_minor: int
    #: 3.1(e) — non-GST outward supplies (alcohol, petroleum, …). Always zero: `supply_type`
    #: carries no such classification, and inventing one without a real non-GST product line to
    #: test it against would be guessing.
    non_gst_taxable_minor: int
    #: Not a numbered row of the real form — GSTR-3B has no line of its own for the taxable value
    #: of an outward reverse-charge supply, and folding it into 3.1(a) is exactly the bug this
    #: field exists to avoid. Kept here, visibly, rather than dropped: the value was real, the
    #: platform just does not owe the tax on it.
    outward_reverse_charge: TaxHeadAmounts
    outward_reverse_charge_taxable_minor: int

    @property
    def output_tax(self) -> TaxHeadAmounts:
        """The liability Table 6.1 is computed against — (a) and (b) summed, since (c)/(d)/(e)
        never carry tax by construction and a reverse-charge outward supply is the recipient's
        liability, not this organization's."""
        return self.taxable_supplies + self.zero_rated


@dataclass(frozen=True, slots=True)
class Gstr3bItc:
    """Table 4, mapped the way Notification 14/2022-CT reshaped it (with effect from the return
    for July 2022): 4A(5) is **gross** eligible-by-default ITC — including credit this platform
    never let onto the spendable balance because a category blocked it under s. 17(5) — 4B splits
    the reversal the return itself asks for into two rows, 4C is the net the ledger actually
    carries forward, and 4D is informational and never touches 4C.

    * **4A(5) "All other ITC"** — every accepted document's credit, blocked or not. 4A(1)-(4) —
      import of goods, import of services, inward supplies liable to RCM, and credit from an Input
      Service Distributor — each ask for a fact `ims_documents` does not carry: nothing
      distinguishes an import or an RCM inward supply or an ISD credit from an ordinary domestic
      purchase today, so this platform cannot place a figure in any of the four.
    * **4B(1) "As per Rule 42/43 and s. 17(5)"** — the blocked-category portion of 4A(5), read
      off `ims_decisions.expense_category` rather than the ledger, because a blocked document
      never posts a ledger row to begin with (see the module note above the ledger — nothing to
      spend is nothing to post). Rules 42/43 themselves (the exempt-supply proportional reversal)
      are unmodelled; only s. 17(5) is.
    * **4B(2) "Others"** — reversals actually posted to the ledger this period: an accepted
      document's status later moving away from `accepted`, or a supplier credit note accepted
      against credit already claimed. Rule 37 (non-payment within 180 days) is unmodelled.
    * **4C = 4A - 4B** — the figure `payment` actually utilises. Numerically unchanged by this
      remapping: 4A(5) gross minus 4B(1) blocked minus 4B(2) other is exactly the eligible accrual
      minus the ledger's own reversals, which is what `net_eligible` computed before the return's
      *display* shape was corrected to match the notification.
    * **4D(1) "ITC reclaimed, reversed earlier under 4B(2)"** and **4D(2) "Ineligible under
      s. 16(4) / place-of-supply"** — both always zero. Neither Rule 37's 180-day re-claim nor a
      time-barred or PoS-mismatched claim is modelled; see the module note.
    """

    available: TaxHeadAmounts  # 4A(5), gross
    reversed_section_17_5: TaxHeadAmounts  # 4B(1)
    reversed_other: TaxHeadAmounts  # 4B(2)
    reclaimed: TaxHeadAmounts  # 4D(1) — always zero, out of scope
    ineligible_other: TaxHeadAmounts  # 4D(2) — always zero, out of scope

    @property
    def total_reversed(self) -> TaxHeadAmounts:  # 4B = 4B(1) + 4B(2)
        return self.reversed_section_17_5 + self.reversed_other

    @property
    def net_eligible(self) -> TaxHeadAmounts:  # 4C = 4A - 4B
        return self.available - self.total_reversed


@dataclass(frozen=True, slots=True)
class Gstr3bPayment:
    """Table 6.1 — payment of tax: the liability, what credit covered it and how, and what is
    left for cash. Late fee and interest are out of scope — see the module note."""

    liability: TaxHeadAmounts
    utilisation: UtilisationResult

    @property
    def cash_payable(self) -> TaxHeadAmounts:
        return self.utilisation.cash_payable


@dataclass(frozen=True, slots=True)
class Gstr3bReturn:
    """One period's GSTR-3B, assembled. Pure data — the snapshot a `gst_returns` row stores."""

    period: str
    gstin: str
    outward: Gstr3bOutward
    #: The count of reportable documents Table 3.1 was summed from — denormalised the same way
    #: `Gstr1Return.totals.document_count` is, so a list page need not re-open the snapshot.
    document_count: int
    itc: Gstr3bItc
    #: The ITC ledger balance brought into the period, before this period's accrual and reversal —
    #: what Table 6.1 has *on top of* 4C to utilise.
    itc_brought_forward: TaxHeadAmounts
    payment: Gstr3bPayment


def assemble_gstr3b(
    documents: Sequence[ReturnDocument],
    *,
    period: str,
    gstin: str,
    itc_brought_forward: TaxHeadAmounts,
    itc_accrued: TaxHeadAmounts,
    itc_reversed: TaxHeadAmounts,
    itc_blocked: TaxHeadAmounts,
) -> Gstr3bReturn:
    """Assemble one period's GSTR-3B. Pure, like `assemble_gstr1`, and deliberately built from the
    *same* raw document list rather than from an already-assembled `Gstr1Return` — two independent
    paths over one book, so a test comparing this return's Table 3.1 against GSTR-1's totals for
    the same period is a real cross-foot rather than a tautology built from itself.

    `documents` is every finalized document the organization issued in the period, exactly what
    `assemble_gstr1` is handed — the two returns are always generated over the same book. The three
    `itc_*` arguments are the ledger's own figures for the period, computed by `selectors` from the
    ITC ledger table; nothing here writes to it or decides what belongs in it.
    """
    parse_period(period)
    reported = [d for d in documents if is_reportable_in_return(d.document_type, d.status)]

    taxable_split = ZERO_TAX
    taxable_minor = 0
    zero_rated_split = ZERO_TAX
    zero_rated_minor = 0
    reverse_charge_split = ZERO_TAX
    reverse_charge_minor = 0
    for document in reported:
        if document.reverse_charge:
            # The recipient owes this tax, not this organization — reported for visibility, never
            # folded into (a) or (b), and never part of `output_tax`.
            reverse_charge_split = reverse_charge_split + document.split
            reverse_charge_minor += document.taxable_minor
        elif document.supply_type in INTERSTATE_SUPPLY_TYPES:
            zero_rated_split = zero_rated_split + document.split
            zero_rated_minor += document.taxable_minor
        else:
            taxable_split = taxable_split + document.split
            taxable_minor += document.taxable_minor

    outward = Gstr3bOutward(
        taxable_supplies=TaxHeadAmounts(
            igst_minor=taxable_split.igst_minor,
            cgst_minor=taxable_split.cgst_minor,
            sgst_minor=taxable_split.sgst_minor,
        ),
        outward_taxable_minor=taxable_minor,
        zero_rated=TaxHeadAmounts(
            igst_minor=zero_rated_split.igst_minor,
            cgst_minor=zero_rated_split.cgst_minor,
            sgst_minor=zero_rated_split.sgst_minor,
        ),
        zero_rated_taxable_minor=zero_rated_minor,
        nil_rated_taxable_minor=0,
        reverse_charge_inward_taxable_minor=0,
        non_gst_taxable_minor=0,
        outward_reverse_charge=TaxHeadAmounts(
            igst_minor=reverse_charge_split.igst_minor,
            cgst_minor=reverse_charge_split.cgst_minor,
            sgst_minor=reverse_charge_split.sgst_minor,
        ),
        outward_reverse_charge_taxable_minor=reverse_charge_minor,
    )

    itc = Gstr3bItc(
        available=itc_accrued + itc_blocked,
        reversed_section_17_5=itc_blocked,
        reversed_other=itc_reversed,
        reclaimed=ZERO_ITC,
        ineligible_other=ZERO_ITC,
    )

    liability = outward.output_tax
    available_for_utilisation = itc_brought_forward + itc.net_eligible
    utilisation = utilise_itc(available_for_utilisation, liability)

    return Gstr3bReturn(
        period=period,
        gstin=gstin,
        outward=outward,
        document_count=len(reported),
        itc=itc,
        itc_brought_forward=itc_brought_forward,
        payment=Gstr3bPayment(liability=liability, utilisation=utilisation),
    )


def gstr3b_reconciles_with_gstr1(gstr3b: Gstr3bReturn, gstr1_totals: ReturnTotals) -> bool:
    """Table 3.1's outward figures must equal GSTR-1's totals for the same period, to the paisa.

    Both returns are assembled from the same documents, so this should always hold. GSTR-1 reports
    every outward document regardless of `reverse_charge`, so the comparison adds
    `outward_reverse_charge` back in even though `output_tax` (what Table 6.1 collects cash
    against) deliberately excludes it — the two questions are "what did this book declare" and
    "what does this organization itself owe", and only the first is what GSTR-1 answers too.
    Table 3.1(c), (d) and (e) are always zero given what this platform records, so (a) + (b) + the
    reverse-charge figure is the *whole* of this platform's outward declaration. Checked at
    generation time as a defensive assertion, not only in a test: two returns silently disagreeing
    about one book is exactly the mismatch this pairing exists to prevent.
    """
    outward = (
        gstr3b.outward.taxable_supplies
        + gstr3b.outward.zero_rated
        + gstr3b.outward.outward_reverse_charge
    )
    outward_taxable = (
        gstr3b.outward.outward_taxable_minor
        + gstr3b.outward.zero_rated_taxable_minor
        + gstr3b.outward.outward_reverse_charge_taxable_minor
    )
    return (
        outward_taxable == gstr1_totals.taxable_minor
        and outward.cgst_minor == gstr1_totals.cgst_minor
        and outward.sgst_minor == gstr1_totals.sgst_minor
        and outward.igst_minor == gstr1_totals.igst_minor
    )


# --------------------------------------------------------------------- e-Way bills
# A second portal with a second obligation, and it is not e-invoicing with a different endpoint:
# an IRN says a supply was reported, an e-Way bill says goods are *moving right now* and is
# checked at the roadside. That is why it expires and an IRN does not.

#: Goods moving under a consignment worth more than this need an e-Way bill. ₹50,000, in paise.
#: Strictly greater, like the B2CL threshold and for the same reason.
EWAY_THRESHOLD_MINOR: int = 50_000_00

#: How the goods travel. The portal wants a vehicle number for road and a transport document
#: number for everything else, which is a real branch rather than a formatting difference.
TRANSPORT_MODES: frozenset[str] = frozenset({"road", "rail", "air", "ship"})

#: The lifecycle of one attempt, and of the bill it produces.
#:
#:   pending    — an attempt recorded before an answer came back (a portal outage).
#:   generated  — the portal issued a bill number; it is live until `valid_until`.
#:   failed     — the portal refused. Not one of the four states a *bill* has, because an attempt
#:                that never became a bill has no bill lifecycle; it is here for the same reason
#:                `einvoice_filings` carries it, since this table records attempts and not bills.
#:   cancelled  — a generated bill was withdrawn inside the 24-hour window.
#:   expired    — validity ran out. **Never written by the live path** — it is derived on read
#:                from the stored `valid_until`, because the table is append-only and a row
#:                cannot be updated when the clock passes it. Storable so a bill imported from
#:                the portal already past its validity is representable.
#:   submitting — the **claim** for a generation (D3, ADR-0015 addendum): the transport facts,
#:                committed before the portal is called with no transaction open. The facts are
#:                the caller's and are on no invoice, so a claim row is the only place a resumed
#:                attempt can find them. Idempotent by (document, transporter/vehicle part) —
#:                `eway_idempotency_key` — and the portal's own answer to a repeat is "already
#:                generated", carrying the bill number (success-by-lookup,
#:                `EwayAlreadyGenerated`).
#:   cancelling — the **claim** for a withdrawal, carrying the reason and the bill's own
#:                validity, audited as `eway.cancel_requested`. A portal that answers "already
#:                cancelled" is success-by-fetch (`EwayAlreadyCancelled`).
#: A `submitting` or `cancelling` row still newest for its document past `resume_after` is what
#: the reclaim sweep re-drives; so is a `pending` (outage) row, which nothing else retries yet.
EWAY_STATUSES: frozenset[str] = frozenset(
    {"pending", "generated", "failed", "cancelled", "expired", "submitting", "cancelling"}
)

#: Kilometres of approximate distance one day of validity buys, for ordinary cargo.
EWAY_KM_PER_DAY: int = 200

#: The same, for over-dimensional cargo — a tenth of the distance for the same day, because it
#: moves under escort and at a fraction of the speed.
EWAY_ODC_KM_PER_DAY: int = 20

#: The portal refuses a distance outside this band: zero is not a movement, and 4,000 km is
#: further than any road journey inside India.
EWAY_MIN_DISTANCE_KM: int = 1
EWAY_MAX_DISTANCE_KM: int = 4_000

#: How long after generation an e-Way bill may be withdrawn. Same 24 hours as an IRN, and for
#: the same reason it is enforced locally: the answer is arithmetic over a stored timestamp.
EWAY_CANCELLATION_WINDOW: timedelta = timedelta(hours=24)

#: The portal's cancellation reasons. Four, like the IRP's, and no free-text alternative.
EWAY_CANCELLATION_REASONS: dict[str, str] = {
    "1": "Duplicate",
    "2": "Order cancelled",
    "3": "Data entry mistake",
    "4": "Others",
}


def requires_eway_bill(
    *, consignment_value_minor: int, document_type: str, supply_type: str, status: str
) -> bool:
    """Whether this document's goods movement needs an e-Way bill.

    Three conditions, and the value one is strict: *more than* ₹50,000. The absolute value is
    taken because a credit note's negative total still describes goods physically moving — back
    to the seller — and a return consignment needs a bill exactly as the outward one did.

    A draft or a void is excluded for the same reason it is excluded from filing: nothing is
    moving under a document nobody issued.
    """
    if status in UNREPORTED_STATUSES or document_type not in DOCUMENT_TYPES:
        return False
    if supply_type not in SUPPLY_TYPES:
        return False
    return abs(consignment_value_minor) > EWAY_THRESHOLD_MINOR


def eway_validity_days(distance_km: int, *, over_dimensional: bool = False) -> int:
    """How many days of validity `distance_km` buys. One day per 200 km **or part thereof**.

    "Or part thereof" is the whole rule: 201 km is two days, not one and a bit. Minimum one, so a
    5 km delivery is still valid for a day rather than for none.
    """
    if distance_km < EWAY_MIN_DISTANCE_KM or distance_km > EWAY_MAX_DISTANCE_KM:
        raise ValueError(
            f"{distance_km} km is not a distance the portal accepts "
            f"({EWAY_MIN_DISTANCE_KM} to {EWAY_MAX_DISTANCE_KM} km)"
        )
    per_day = EWAY_ODC_KM_PER_DAY if over_dimensional else EWAY_KM_PER_DAY
    return max(1, -(-distance_km // per_day))


def eway_valid_until(
    valid_from: datetime, distance_km: int, *, over_dimensional: bool = False
) -> datetime:
    """When a bill generated at `valid_from` for `distance_km` stops being valid.

    The statutory shape, which is *not* "generation time plus N x 24 hours": each day of validity
    expires at midnight of the day immediately following the date of generation. So a bill
    generated at 23:50 on the 1st for 150 km expires at midnight ending the 1st — ten minutes
    later — and a driver who reads it as "a day from now" is stopped at a checkpoint.

    Computed here, from the two values the platform *stores*, so the deadline a screen shows and
    the deadline enforced are the same arithmetic over the same timestamp — never a figure the
    portal returned and nothing can re-derive.
    """
    days = eway_validity_days(distance_km, over_dimensional=over_dimensional)
    end_date = valid_from.date() + timedelta(days=days)
    return datetime.combine(end_date, datetime.min.time(), tzinfo=valid_from.tzinfo or UTC)


def eway_expired(valid_until: datetime, at: datetime) -> bool:
    """Whether validity has run out at `at`. Inclusive of the instant it ends: at midnight the
    bill is already expired, which is the direction that fails safe at a checkpoint."""
    return at >= valid_until


def eway_cancellation_deadline(generated_at: datetime) -> datetime:
    return generated_at + EWAY_CANCELLATION_WINDOW


def eway_cancellation_window_open(generated_at: datetime, at: datetime) -> bool:
    """Whether a generated bill may still be withdrawn at `at`."""
    return at - generated_at <= EWAY_CANCELLATION_WINDOW


#: How old the first row of a stuck e-Way generation chain may be before the reclaim sweep stops
#: re-driving it and records a terminal `stale` row instead. A bill is a licence for goods to be
#: on the road now; a consignment that should have moved a day ago is a person's call (D3).
EWAY_REDRIVE_MAX_AGE: timedelta = timedelta(hours=24)


def eway_idempotency_key(
    *,
    invoice_id: uuid.UUID,
    generation: int,
    transport_mode: str,
    vehicle_no: str | None,
    transporter_id: str | None,
    transport_doc_no: str | None,
    transport_doc_date: date | None,
    distance_km: int,
    over_dimensional: bool,
) -> str:
    """The key one e-Way generation is idempotent by: **(document, transporter/vehicle part)**,
    plus which bill of the document's life this is.

    * **document** — the invoice id, which fixes the seller, the document type, number and date.
    * **transporter/vehicle part** — "Part B" of the bill: how the goods move and who moves them.
      The same consignment asked for twice with the same Part B is one bill; a different vehicle
      is a different request, and must not be answered with the first one's number.
    * **distance and over-dimensional cargo** — not Part B, but the two inputs this platform
      computes validity from (`eway_valid_until`). A repeat answered "already generated" is
      recorded against the *resuming* row's stored distance; were the distance not in the key, a
      corrected distance could be answered with a bill raised for the old one, and the deadline
      a screen shows would no longer be the portal's.
    * **generation** — 1 for the document's first bill, 2 once that one was cancelled or expired
      and another is raised, and so on (the count of `generated` rows before this claim, plus
      one). Without it a document legitimately re-raised on the same lorry after a cancellation
      would be answered with the withdrawn bill.

    A pure function of values stored on the claim row and the ledger, so a resumed attempt
    (`app/cron.py`, after a crash between the portal call and recording it) recomputes the
    identical key without anything extra having been persisted, and a real portal answers the
    repeat with "already generated" (`EwayAlreadyGenerated`) rather than a second bill.
    """
    if generation < 1:
        raise ValueError("e-Way generations are 1-based")
    part_b = "|".join(
        [
            transport_mode,
            _normalised_ref(vehicle_no),
            _normalised_ref(transporter_id),
            _normalised_ref(transport_doc_no),
            transport_doc_date.isoformat() if transport_doc_date else "",
            str(distance_km),
            "odc" if over_dimensional else "",
        ]
    )
    digest = hashlib.sha256(part_b.encode()).hexdigest()[:24]
    return f"eway:{invoice_id}:{generation}:{digest}"


def _normalised_ref(value: str | None) -> str:
    """A vehicle number, transporter id or transport document number as the portal compares it:
    case- and whitespace-insensitive, so `mh12 ab1234` and `MH12AB1234` are one request."""
    return "".join((value or "").split()).upper()


def chain_start(rows: Sequence[Any], *, chain: frozenset[str]) -> Any | None:
    """The oldest row of the run of `chain` statuses at the head of a newest-first history — when
    the stuck attempt was first made, which is what the age cap on re-driving it measures."""
    first = None
    for row in rows:
        if row.status not in chain:
            break
        first = row
    return first


def leading_claim_attempts(statuses: Sequence[str], *, claim: str, chain: frozenset[str]) -> int:
    """How many times the claim at the head of a newest-first history has been driven.

    Walks `statuses` from the newest while each is in `chain` (the claim status and the outage
    rows its attempts leave behind), counting the `claim` rows among them. A `cancelling` chain
    is just its claim rows — an outage appends nothing, the claim standing is the record — while
    an e-Way generation chain interleaves `submitting` claims with the `pending` rows each
    outage recorded, and only the claims count as drives. The reclaim sweep's poisoned-step
    guard compares this to `MAX_COMPLIANCE_RESUMES`; `resume_after` turns it into the backoff.
    """
    attempts = 0
    for status in statuses:
        if status not in chain:
            break
        if status == claim:
            attempts += 1
    return attempts


def eway_status_at(*, status: str, valid_until: datetime | None, at: datetime) -> str:
    """The status a stored attempt *actually* has now.

    The only place `expired` is produced. A `generated` row whose validity has passed reads as
    `expired`; every other status is what it was, because a cancelled bill does not later expire
    and a refusal has no validity to run out.
    """
    if status == "generated" and valid_until is not None and eway_expired(valid_until, at):
        return "expired"
    return status


def eway_remedy_for(*, status: str, generated_at: datetime | None, at: datetime) -> str | None:
    """What can still be done to a bill: `cancel` inside the window, otherwise nothing.

    Named as a single answer rather than a set of flags for the same reason `remedy_for` is: a
    screen must offer the one action that will work, and offering both is how a user learns
    which is right by having the other fail.

    A failed *withdrawal* (a `failed` row carrying the bill's `valid_from` — a failed generation
    never has one) leaves the bill standing: ask again inside the window, `contact_support`
    outside it, where only the portal knows whether it holds a withdrawal never confirmed (D3).
    """
    if generated_at is None:
        return None
    if status == "generated":
        return "cancel" if eway_cancellation_window_open(generated_at, at) else None
    if status == "failed":
        return "cancel" if eway_cancellation_window_open(generated_at, at) else "contact_support"
    return None


# --------------------------------------------------------------------- withholding: TDS and TCS
# Two different statutes that both take a slice off an invoice, and they are not variants of each
# other:
#
#   GST s. 51    the *recipient* deducts 2 % of the taxable value and pays it to the GST
#                authority, on notified supplies (metal scrap is the case this platform's
#                vertical meets) above ₹2.5 lakh under one contract. It splits across tax heads
#                exactly as GST does, because it *is* GST.
#   IT s. 194Q   the *buyer* deducts 0.1 % of purchases from one seller beyond ₹50 lakh in a
#                financial year. Income tax, no heads, cumulative rather than per-invoice.
#   IT s. 206C(1H)  the *seller* collects 0.1 % of receipts from one buyer beyond the same
#                ₹50 lakh — and must **not** when the buyer is already deducting under 194Q.
#
# All three are versioned by `effective_from` the way `tax_rates` is, and for the sharper reason:
# a rate change here must not restate history. The rate that applied is the one in force on the
# *document's* date, resolved once and snapshotted onto the row, so adding tomorrow's rate today
# changes nothing that was already computed.

#: The withholding heads this service models.
WITHHOLDING_CODES: frozenset[str] = frozenset({"gst_tds_51", "itax_194q", "itax_206c_1h"})

#: How each one measures its threshold.
#:
#:   supply     — per document, against that document's own taxable value (GST s. 51).
#:   aggregate  — against the running financial-year total with that party, so the rate bites
#:                only on the part above the threshold (194Q, 206C(1H)).
WITHHOLDING_BASES: frozenset[str] = frozenset({"supply", "aggregate"})

#: Which side of the invoice the money leaves from.
#:
#:   deducted   — somebody withheld it from what they owe us (s. 51, 194Q). We are paid less and
#:                claim the credit.
#:   collected  — we charged it on top and owe it onward (206C(1H)).
WITHHOLDING_DIRECTIONS: frozenset[str] = frozenset({"deducted", "collected"})

WITHHOLDING_DIRECTION: dict[str, str] = {
    "gst_tds_51": "deducted",
    "itax_194q": "deducted",
    "itax_206c_1h": "collected",
}


@dataclass(frozen=True, slots=True)
class WithholdingRate:
    """One version of one withholding head. Immutable, and superseded rather than edited."""

    id: uuid.UUID
    code: str
    rate_pct: Decimal
    threshold_minor: int
    basis: str
    effective_from: date
    effective_to: date | None
    jurisdiction: str = "IN"

    def covers(self, on: date) -> bool:
        """Half-open: `[effective_from, effective_to)`, so a version ending the day another
        begins leaves neither a gap nor an overlap on the changeover date."""
        if on < self.effective_from:
            return False
        return self.effective_to is None or on < self.effective_to


@dataclass(frozen=True, slots=True)
class Withholding:
    """One computed deduction or collection — the row that goes in the register.

    Carries the resolved rate and threshold as *snapshots* beside the id of the version they
    came from, which is what makes "why is this figure what it is" answerable years later
    without depending on the schedule still saying the same thing.
    """

    code: str
    rate_id: uuid.UUID
    rate_pct: Decimal
    threshold_minor: int
    basis: str
    direction: str
    #: The amount the rate was applied to — the part above the threshold, not the whole invoice.
    basis_minor: int
    amount_minor: int
    cgst_minor: int = 0
    sgst_minor: int = 0
    igst_minor: int = 0


def resolve_withholding_rate(
    schedule: Sequence[WithholdingRate], code: str, on: date, *, jurisdiction: str = "IN"
) -> WithholdingRate | None:
    """The version of `code` in force on `on` — the document's date, never today's.

    Resolving against the document's date is the whole of "a rate change must not restate
    history": a new row with a later `effective_from` is invisible to every document that came
    before it, so no recomputation of an old invoice can produce a new answer.

    The newest covering version wins, so a correction issued as a second row with the same
    `effective_from` supersedes the first rather than being ambiguous.
    """
    covering = [
        rate
        for rate in schedule
        if rate.code == code and rate.jurisdiction == jurisdiction and rate.covers(on)
    ]
    if not covering:
        return None
    return max(covering, key=lambda r: (r.effective_from, r.id.int))


def withholding_amount_minor(basis_minor: int, rate_pct: Decimal) -> int:
    """A withholding, in minor units, rounded half-up **once**.

    Deliberately the same shape as `gst_amount_minor`, and deliberately not a call to it: that
    function is the GST levy and this is a deduction under a different statute. They agree today
    at 2 % of a taxable value and they are not the same rule, so they are not the same function.
    """
    exact = Decimal(basis_minor) * rate_pct / Decimal(100)
    return int(exact.to_integral_value(rounding=ROUND_HALF_UP))


def supply_withholding_basis(taxable_minor: int, threshold_minor: int) -> int:
    """What a per-supply head applies to: the whole taxable value, or nothing.

    Not "the excess". GST s. 51 is a cliff — below the threshold nothing is deducted, above it
    the 2 % is on the *entire* taxable value — which is the opposite of how 194Q works and the
    single most common way the two get conflated.
    """
    return taxable_minor if abs(taxable_minor) > threshold_minor else 0


def aggregate_withholding_basis(
    *, prior_minor: int, current_minor: int, threshold_minor: int
) -> int:
    """What an aggregate head applies to: this document's share of the excess over the threshold.

    The rate bites only on the part of the financial year's running total that sits above the
    threshold, so the invoice that *crosses* ₹50 lakh is withheld on partially — the part above —
    and every one after it in full. Computed as the difference of two excesses rather than with
    a branch, so the three cases (wholly below, straddling, wholly above) are one expression and
    the straddling one cannot be the case somebody forgot.
    """
    excess_before = max(0, prior_minor - threshold_minor)
    excess_after = max(0, prior_minor + current_minor - threshold_minor)
    return excess_after - excess_before


def income_tax_head(*, buyer_deducts_194q: bool) -> str | None:
    """Which income-tax head applies to a sale, when either could.

    194Q and 206C(1H) are mutually exclusive by statute and the tie is not a coin toss: where the
    buyer is liable to deduct under 194Q, the seller must **not** collect under 206C(1H). Charging
    both is the error, and it is the error a naive "apply every rule that matches" produces.
    """
    return "itax_194q" if buyer_deducts_194q else "itax_206c_1h"


def compute_gst_tds(
    *,
    taxable_minor: int,
    rate: WithholdingRate,
    intra_state: bool,
) -> Withholding | None:
    """GST s. 51 on one supply, split across the heads it is payable under.

    The split reuses `split_gst`, which is the point: a 2 % deduction is 1 % CGST + 1 % SGST
    intra-state and 2 % IGST inter-state, with the odd paisa landing in the same place it lands
    on the levy itself. Re-halving it here would be the second implementation of the platform's
    one calculation that has to foot exactly.
    """
    basis = supply_withholding_basis(taxable_minor, rate.threshold_minor)
    if not basis:
        return None
    split = split_gst(basis, rate.rate_pct, intra_state=intra_state)
    return Withholding(
        code=rate.code,
        rate_id=rate.id,
        rate_pct=rate.rate_pct,
        threshold_minor=rate.threshold_minor,
        basis=rate.basis,
        direction=WITHHOLDING_DIRECTION[rate.code],
        basis_minor=basis,
        amount_minor=split.total_minor,
        cgst_minor=split.cgst_minor,
        sgst_minor=split.sgst_minor,
        igst_minor=split.igst_minor,
    )


def compute_income_tax_withholding(
    *, prior_minor: int, current_minor: int, rate: WithholdingRate
) -> Withholding | None:
    """194Q or 206C(1H) on one invoice, given the year's running total before it.

    No tax heads: income tax is not GST, so `cgst/sgst/igst` stay zero rather than being given a
    meaning they do not have.
    """
    basis = aggregate_withholding_basis(
        prior_minor=prior_minor,
        current_minor=current_minor,
        threshold_minor=rate.threshold_minor,
    )
    if not basis:
        return None
    return Withholding(
        code=rate.code,
        rate_id=rate.id,
        rate_pct=rate.rate_pct,
        threshold_minor=rate.threshold_minor,
        basis=rate.basis,
        direction=WITHHOLDING_DIRECTION[rate.code],
        basis_minor=basis,
        amount_minor=withholding_amount_minor(basis, rate.rate_pct),
    )


def financial_year_bounds(on: date) -> tuple[date, date]:
    """India's financial year around `on`, as a half-open `[start, end)` pair — April to April.

    The window an aggregate threshold is measured over, returned rather than reconstructed at
    each call site so "which year is this invoice in" has exactly one answer.
    """
    start_year = on.year if on.month >= 4 else on.year - 1
    return date(start_year, 4, 1), date(start_year + 1, 4, 1)


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


# --------------------------------------------------------------------- revenue history
# The real 12-month series `/finance/overview.revenue_history` serves, replacing the sample trend
# a dashboard would otherwise have to invent. Unlike the KPIs above (anchored to `_today`, in UTC
# — see `selectors._today`, left as-is here since changing it would move every other overview and
# invoice-list figure, not just this one), this series resolves "today" in the organization's own
# timezone. Finance carries no per-organization timezone setting of its own today (unlike
# `leave.LeaveSettings`), so `DEFAULT_TIMEZONE` is the documented fallback until one exists — the
# same "missing setting is the IST default" rule `leave-service` already applies.
DEFAULT_TIMEZONE = "Asia/Kolkata"


def today_in(timezone: str, *, now: datetime) -> date:
    """ "Today" as this organization's clock would read it, never the bare UTC date a naive
    `datetime.now(UTC).date()` would give near a midnight that has already turned over locally."""
    return now.astimezone(ZoneInfo(timezone)).date()


def month_key(d: date) -> str:
    """A date's calendar month as `YYYY-MM` — the series' bucket key."""
    return d.strftime("%Y-%m")


def month_label(month: str) -> str:
    """`YYYY-MM` rendered the way the chart's x-axis reads it, e.g. `Sep 2026`."""
    return datetime.strptime(month, "%Y-%m").strftime("%b %Y")


def add_months(month: str, delta: int) -> str:
    """`YYYY-MM` shifted by `delta` calendar months (negative moves back in time)."""
    year, mon = (int(part) for part in month.split("-"))
    total = year * 12 + (mon - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def month_end(month: str) -> date:
    """The last calendar day of `YYYY-MM` — the anchor a month-end snapshot is struck against."""
    year, mon = (int(part) for part in add_months(month, 1).split("-"))
    return date(year, mon, 1) - timedelta(days=1)


def trailing_months(anchor_month: str, span: int) -> list[str]:
    """The `span` months ending at (and including) `anchor_month`, oldest first — the x-axis of
    every history series here."""
    return [add_months(anchor_month, -offset) for offset in range(span - 1, -1, -1)]


def delta_pct(current: int, previous: int) -> float | None:
    """Percentage change vs. a prior period, to one decimal place. `None` when the prior period
    has nothing to compare against — a percentage computed against zero is a divide-by-zero
    dressed up as a number, and an invented delta is worse than none (§5.0 of the dashboard audit:
    "An invented delta is worse than no delta")."""
    if previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


@dataclass(frozen=True, slots=True)
class InvoiceHistoryFacts:
    """What one document needs to take part in the multi-month, multi-currency history, beyond
    what `InvoiceFacts` carries for the single-instant overview: its own currency (the book's real
    partition — ADR-0000 §8, money is never summed across currencies) and every allocation ever
    applied to it, so a past month's balance can be replayed rather than read off today's."""

    currency: str
    document_type: str
    #: The stored override only (`draft`/`void`/`paid`/…), never the derived-for-today status —
    #: `paid` is time-dependent (set the instant a payment settles it) and would misreport an
    #: earlier month as already settled, so history recomputes it fresh from `allocations` at
    #: each month-end instead. Only `draft`/`void` are true create-time facts, invariant over time.
    stored_status: str
    issued_date: date
    total_minor: int
    #: `(payment.received_date, amount_minor)` per allocation ever posted against this document.
    allocations: tuple[tuple[date, int], ...]


@dataclass(frozen=True, slots=True)
class MonthlyRevenuePoint:
    month: str
    label: str
    revenue_minor: int
    received_minor: int
    outstanding_minor: int


@dataclass(frozen=True, slots=True)
class CurrencyRevenueHistory:
    currency: str
    revenue_current_minor: int
    revenue_previous_minor: int
    received_current_minor: int
    received_previous_minor: int
    outstanding_current_minor: int
    outstanding_previous_minor: int
    months: tuple[MonthlyRevenuePoint, ...]


def revenue_history(
    facts: list[InvoiceHistoryFacts], *, anchor_month: str, span: int = 12
) -> list[CurrencyRevenueHistory]:
    """Real, per-currency revenue history: `span` months (oldest first, ending at
    `anchor_month`) of net revenue issued, cash received and receivables outstanding, plus the
    current-vs-previous-month figures those deltas are built from.

    **Never summed across currencies** — one entry per currency this tenant has actually issued
    or been paid in, sorted for a stable response. Draft and void documents are excluded
    everywhere, exactly as `net_revenue_for_month`/`total_outstanding` exclude them. A credit
    note reduces the month it is issued in (its total is negative) but, matching this platform's
    existing receivable rule, does not net against the invoice it corrects — `total_outstanding`
    already behaves this way and this does not add a new gap, only a history of the same one.
    """
    months = trailing_months(anchor_month, span)
    previous_month = add_months(anchor_month, -1)

    by_currency: dict[str, list[InvoiceHistoryFacts]] = {}
    for f in facts:
        by_currency.setdefault(f.currency, []).append(f)

    out: list[CurrencyRevenueHistory] = []
    for currency in sorted(by_currency):
        live = [f for f in by_currency[currency] if f.stored_status not in ("draft", "void")]

        by_month_revenue: dict[str, int] = {}
        by_month_received: dict[str, int] = {}
        by_month_outstanding: dict[str, int] = {}
        points: list[MonthlyRevenuePoint] = []
        for m in months:
            m_end = month_end(m)
            revenue_minor = sum(f.total_minor for f in live if month_key(f.issued_date) == m)
            received_minor = sum(
                amount
                for f in live
                for received, amount in f.allocations
                if month_key(received) == m
            )
            outstanding_minor = sum(
                max(0, f.total_minor - sum(a for d, a in f.allocations if d <= m_end))
                for f in live
                if f.document_type == "invoice" and f.issued_date <= m_end
            )
            by_month_revenue[m] = revenue_minor
            by_month_received[m] = received_minor
            by_month_outstanding[m] = outstanding_minor
            points.append(
                MonthlyRevenuePoint(
                    month=m,
                    label=month_label(m),
                    revenue_minor=revenue_minor,
                    received_minor=received_minor,
                    outstanding_minor=outstanding_minor,
                )
            )

        out.append(
            CurrencyRevenueHistory(
                currency=currency,
                revenue_current_minor=by_month_revenue[anchor_month],
                revenue_previous_minor=by_month_revenue.get(previous_month, 0),
                received_current_minor=by_month_received[anchor_month],
                received_previous_minor=by_month_received.get(previous_month, 0),
                outstanding_current_minor=by_month_outstanding[anchor_month],
                outstanding_previous_minor=by_month_outstanding.get(previous_month, 0),
                months=tuple(points),
            )
        )
    return out


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


def effective_invoice_status(
    *,
    document_type: str,
    stored_status: str,
    due_date: date,
    total_minor: int,
    paid_minor: int,
    today: date,
) -> str:
    """What a read reports for an invoice's status — derived fresh, every time, from the frozen
    facts and the real date, never from a status computed once and left to go stale.

    `draft` and `void` are create-time overrides and win outright: nothing about them can be
    re-derived from amounts or dates. `paid` is a projection `record_payment` writes onto the row
    in the same transaction as the allocation that settles it, so once stored it also wins —
    there is no path back from `paid` to `overdue`. Everything else, including a `sent` or a
    legacy `overdue` stored before this derivation existed, is recomputed here: `overdue` is
    never a fact frozen at create, it is `due_date < today` read fresh against the clock this
    call was given.
    """
    override = stored_status if stored_status in ("draft", "void", "paid") else None
    return derive_invoice_status(
        document_type=document_type,
        status_override=override,
        due_date=due_date,
        total_minor=total_minor,
        paid_minor=paid_minor,
        today=today,
    )


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
