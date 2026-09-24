"""The IMS seam — one `Protocol` every Invoice Management System client satisfies.

The mirror image of `app/irp/base.py`, drawn the same way and for the same reason. E-invoicing
reports this organization's documents *outward*; the Invoice Management System is what its
suppliers reported *about* it, and reaching it is the same choice of integrations: GSTN's own
`returns` API directly, or a GSP reselling it. They differ in credentials and envelope, and in
nothing this service reasons about — the documents are the same government schema and the actions
are the same three.

So `ImsClient` speaks documents and each implementation owns the wire. Which concrete one a
process holds is a wiring decision in `app/deps.py`; the deterministic stub this slice ships is
one, a GSP client is another, and no rule above this line changes when the second arrives.

Three properties are encoded here rather than left to each implementation:

* **Fetching is a read, and it is per period.** IMS is a running ledger of what suppliers have
  filed, and it is worked one return period at a time. A client that fetched "everything" would
  make the queue unbounded and the sync non-idempotent, which is the same bug twice.
* **Every document carries the portal's own reference.** `source_ref` is what makes re-syncing a
  period safe: it is the identity of *that row on the portal*, distinct from the identity of the
  supplier's document, which is exactly the distinction a duplicate is.
* **Amounts are integer minor units, all the way to the wire.** The government schema is written
  in rupees with two decimal places; converting is the adapter's job, at the last moment. Nothing
  above this line divides by 100.

**Pushing decisions back (C2).** A decision recorded here is the platform's own position, appended
to its own ledger; `push_ims_decisions` is how GSTN is told. It is a second call with a second set
of properties, encoded here for the same reason the first three are:

* **Batched per period, within the portal's limit.** One call carries the decisions of one return
  period, at most `push_batch_limit` of them; the caller splits a larger set.
* **Idempotent by (period, document, decision version).** Every item carries the key
  (`domain.ims_push_key`); a portal that already holds a decision under it answers
  `already_recorded` — success by fetch, exactly as `EwayAlreadyGenerated` is for a bill — so a
  crash between the call and the record is repaired by asking again.
* **Answers per document.** A whole call can fail (`ImsUnavailable`, `ImsRateLimited`,
  `ImsPushRejected`), but a batch that reached the portal is answered item by item: some recorded,
  some refused, some in conflict with what GSTN already holds.
* **The taxpayer's action travels back on the fetch.** `ImsSupplierDocument.action` is what GSTN
  currently records for the row, so the next sync can tell whether a push landed (`drift`).

Which failures are retryable is the adapter's to classify, and this is the contract: a timeout, a
connection failure, an HTTP 5xx, a rate limit, or a session it could not refresh is
`ImsUnavailable`; a validation answer about the *request* (a period the portal will not take, a
return already filed) is `ImsPushRejected`; a validation answer about one *document* is an item
with outcome `rejected`; a disagreement with what the portal holds is `conflict`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol, runtime_checkable


class ImsUnavailable(Exception):
    """The portal could not be reached or did not answer in time.

    The only failure a **fetch** has. There is no `ImsRejected` counterpart to the IRP's: a read of
    somebody else's filings cannot be refused on its contents, because this service supplies no
    contents — which is why the two seams are shaped alike but not identically. A **push** does
    supply contents, and has its own refusal (`ImsPushRejected`); an outage is retried there too.
    Covers a timeout, a connection failure and an HTTP 5xx alike.
    """


class ImsRateLimited(ImsUnavailable):
    """The portal is up and asking to be left alone (HTTP 429, or its own throttle code): a kind of
    outage, retried, which may say how long it wants (`retry_after`). The push honours it up to the
    platform's own ceiling (`domain.ims_push_retry_delay`)."""

    def __init__(
        self, message: str = "rate limited", *, retry_after: timedelta | None = None
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ImsPushRejected(Exception):
    """The portal refused a whole push — the request, not one document in it: a period it will not
    take, a return already filed for it, a GSTIN it does not know. Carries the portal's own numbered
    error, like `IrpRejected`. Terminal for every decision in the batch: the same request would be
    refused again, so it is never retried automatically."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ImsSupplierDocument:
    """One document a supplier filed against this organization, as the portal presents it.

    Deliberately not the government JSON: this is the neutral shape, and each adapter maps its own
    envelope onto it. The tax heads travel as the supplier declared them — this service does not
    correct somebody else's return, it reconciles against it, and the difference between the two
    is the whole product.
    """

    #: The portal's reference for this row. The ingest's idempotency key, and **not** the identity
    #: of the supplier's document: two portal rows may describe one document, which is precisely
    #: the duplicate the reconciliation flags.
    source_ref: str

    supplier_gstin: str
    supplier_name: str
    document_number: str
    #: invoice | credit_note | debit_note — `domain.IMS_DOCUMENT_TYPES`.
    document_type: str
    document_date: date
    #: The return period this document lands in, `YYYY-MM`.
    period: str

    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    total_minor: int

    #: The supplier's own IRN, where they e-invoiced. Carried verbatim; not this service's to
    #: verify, and null for a document below the e-invoicing threshold.
    irn: str | None = None

    #: What the portal currently records as **this taxpayer's** action on the row, in the portal's
    #: vocabulary (`domain.IMS_PORTAL_ACTIONS`: `accepted`, `rejected`, `pending`, `no_action`) —
    #: what a pushed decision should have become. `None` when the adapter cannot say, which is
    #: silence and never read as disagreement (`domain.ims_is_drift`).
    action: str | None = None

    @property
    def tax_minor(self) -> int:
        return self.cgst_minor + self.sgst_minor + self.igst_minor


@dataclass(frozen=True, slots=True)
class ImsPushItem:
    """One decision to tell the portal about."""

    #: The portal's reference for the row being decided (`ImsSupplierDocument.source_ref`).
    source_ref: str
    #: The portal's word for the action (`domain.IMS_PORTAL_ACTIONS`).
    action: str
    #: The decision's ordinal within its document: 1 for the first, 2 after a change of mind.
    version: int
    #: `domain.ims_push_key(period, source_ref, version)` — what the portal deduplicates on.
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ImsPushResult:
    """The portal's answer for one document of a push."""

    source_ref: str
    #: `recorded` — the portal took it; `already_recorded` — it already held this decision under
    #: this key (success by fetch); `rejected` — it refused this document's decision (`code` and
    #: `message`); `conflict` — it holds something else for the row (`recorded_action` says what).
    outcome: str
    code: str | None = None
    message: str | None = None
    #: What the portal now holds for the row, in its own vocabulary. Set on `already_recorded` and
    #: `conflict`: an "already recorded" for a *different* action is a conflict, not a success.
    recorded_action: str | None = None


@runtime_checkable
class ImsClient(Protocol):
    """The contract `app/services.py` depends on — never a concrete portal."""

    name: str
    #: The most decisions one `push_ims_decisions` call may carry. The caller batches to this.
    push_batch_limit: int

    def fetch(self, *, gstin: str, period: str) -> list[ImsSupplierDocument]:
        """Every document suppliers filed against `gstin` for the return period `period`.

        Raises `ImsUnavailable` when the portal cannot be reached. That is the whole error
        surface; anything else is a bug.
        """
        ...

    def push_ims_decisions(
        self, *, gstin: str, period: str, decisions: Sequence[ImsPushItem]
    ) -> list[ImsPushResult]:
        """Record `decisions` — this taxpayer's actions on documents of `period` — on the portal.

        One result per decision, in any order (matched on `source_ref`). Raises `ImsUnavailable`
        (including `ImsRateLimited`) when the portal cannot be reached — nothing is known to have
        been recorded, and every decision is retried under the same key — and `ImsPushRejected`
        when the portal refuses the request as a whole. Those are the whole error surface;
        anything else is a bug.
        """
        ...
