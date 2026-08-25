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

**This service does not push actions back to the portal, and this Protocol does not pretend it
can.** A decision recorded here is the platform's own position, appended to its own ledger; the
`accept`/`reject` that eventually reaches GSTN is a second integration with its own filing window,
and inventing a `submit()` method nothing implements would be the kind of half-built seam that
reads as finished. It is in the backlog, named.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable


class ImsUnavailable(Exception):
    """The portal could not be reached or did not answer in time.

    The only failure a fetch has. There is no `ImsRejected` counterpart to the IRP's: a read of
    somebody else's filings cannot be refused on its contents, because this service supplies no
    contents — which is why the two seams are shaped alike but not identically.
    """


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

    @property
    def tax_minor(self) -> int:
        return self.cgst_minor + self.sgst_minor + self.igst_minor


@runtime_checkable
class ImsClient(Protocol):
    """The contract `app/services.py` depends on — never a concrete portal."""

    name: str

    def fetch(self, *, gstin: str, period: str) -> list[ImsSupplierDocument]:
        """Every document suppliers filed against `gstin` for the return period `period`.

        Raises `ImsUnavailable` when the portal cannot be reached. That is the whole error
        surface; anything else is a bug.
        """
        ...
