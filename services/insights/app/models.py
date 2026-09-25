"""Tables owned by the insights service (ADR-0020, `docs/insights/data-model.md`).

Everything here lives in the `insights` schema and nothing outside it may hold a foreign key
into it (CLAUDE.md: no cross-schema foreign keys). `org_id` — here `organization_id` — is a
plain indexed UUID everywhere, never a reference into `account.organizations`.

I1 lands the platform scaffold plus the tables that hold no entity-specific business data:

- `org_profile` — the organization's timezone, currency and fiscal year, folded from
  `core.org.*` and the account export (§2).
- `daily_snapshots`, `metric_coverage`, `backfill_runs` — the machinery every later slice's
  entity facts plug into (§5): captured state, per-metric history coverage, and the resumable
  backfill runner's own bookkeeping.
- `outbox`, `processed_events`, `audit_logs` — the scaffold's platform mixins. `outbox` stays
  empty forever (insights publishes nothing) and is kept only for `/health/outbox` parity, the
  same reason `console` keeps its own. `audit_logs` stays empty too: insights has no
  user-initiated write. `processed_events` is the one exemption from "every table carries
  organization_id" (data-model.md §5, ADR risk 8) — the platform mixin every consumer uses.

Slices I2-I9 each add their own `*_facts` table(s) here, following `org_profile`'s shape: a
version-guarded upsert target, keyed `(organization_id, id)` with the source row's own id,
tenant-scoped and RLS-policied like everything else in this file.

I2 adds finance's three: `invoice_facts`, `payment_facts`, `purchase_facts` (data-model.md §3).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy import (
    Date as SADate,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base

SCHEMA = "insights"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe.

    Insights publishes nothing (ADR-0020 §6, data-model.md §5) — kept only so
    `/health/outbox` and the edge relay's own schema list behave the same for every service,
    rather than insights being a special case with no such probe at all.
    """


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once.

    Deliberately un-policied (ADR-0020 risk 8, ADR-0002 §5): it carries no `organization_id`,
    the platform mixin every consumer shares. A policy here would make every
    `already_processed` lookup return nothing and a redelivered event would be reprocessed in
    silence. Correctness does not lean on it anyway — every fact write is *also*
    version-guarded by `source_updated_at` (data-model.md §1.1), so a duplicate fold converges
    on the same row rather than double-applying.
    """


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it.

    Stays empty: insights has no user-initiated write (data-model.md §5). Kept from the
    scaffold for parity with every other service's shape.
    """


class OrgProfile(Base):
    """One row per organization: the settings a projection needs to bucket dates and money for
    it (data-model.md §2). Folded from `core.org.created`/`core.org.updated` (I1) and from the
    account export's own page (`GET /internal/v1/organizations/export`, the backfill runner's
    one I1-registered entity, `app/backfill.py`).

    A fact for an organization with no row here reads the class-level defaults below —
    `Asia/Kolkata`, `INR`, April — and every API response names the timezone it actually used,
    so a fact write is never blocked on this row existing first.
    """

    __tablename__ = "org_profile"

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Asia/Kolkata", server_default="Asia/Kolkata"
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    fiscal_year_start_month: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4, server_default="4"
    )
    #: The version guard, as for every fact (data-model.md §1.1): the envelope `occurred_at` for
    #: `core.org.*`, the source row's own `updated_at` for the account export page.
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "fiscal_year_start_month between 1 and 12", name="fiscal_year_start_month_range"
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_shape"),
    )


#: Snapshot metrics registered so far. Empty in I1 — no fact table exists yet for any of
#: `pipeline_value`, `open_deals`, `weighted_pipeline`, `customers`, `open_tasks`, `stock_value`,
#: `low_stock_items`, `out_of_stock_items` (data-model.md §5). `app/snapshot.py`'s sweep reads
#: this to know what it may capture; a later slice appends to it in the same commit that adds
#: the fact table the capture reads from.
SNAPSHOT_METRICS: tuple[str, ...] = ()


class DailySnapshot(Base):
    """Captured state whose history nothing else keeps (ADR-0020 §2, data-model.md §5):
    written only by the hourly sweep, for the organization's current local day, and never
    rewritten once that day has ended. **Not rebuildable** — a schema restore is the only way
    back if this table is lost, so it rides the same nightly backup as everything else in this
    Postgres and a rebuild must never truncate it (`app/snapshot.py`).
    """

    __tablename__ = "daily_snapshots"

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    metric: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: The currency code for a money metric, so each currency gets its own row and none are
    #: ever summed together; `''` for a metric with no currency dimension.
    dimension: Mapped[str] = mapped_column(
        String(8), primary_key=True, default="", server_default=""
    )
    day: Mapped[date] = mapped_column(SADate, primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("metric ~ '^[a-z][a-z0-9_]*$'", name="metric_shape"),
        CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="daily_snapshots_currency_shape"
        ),
    )


class MetricCoverage(Base):
    """Per organization, per metric: the first day the metric is complete from
    (`docs/insights/metrics.md` §3). Recomputed after every completed backfill run for the
    entity the metric reads, and by the sweep when a snapshot metric's first capture lands.
    `history_from IS NULL` means complete over the source's whole history.
    """

    __tablename__ = "metric_coverage"

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    metric: Mapped[str] = mapped_column(String(64), primary_key=True)
    history_from: Mapped[date | None] = mapped_column(SADate)
    basis: Mapped[str] = mapped_column(String(32), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("metric ~ '^[a-z][a-z0-9_]*$'", name="coverage_metric_shape"),
        CheckConstraint(
            "basis in ('source_dates', 'source_column_from_go_live', 'first_capture')",
            name="basis_vocabulary",
        ),
    )


#: `backfill_runs.status`, in the order a run actually moves through them.
BACKFILL_STATUSES: tuple[str, ...] = ("pending", "running", "done", "failed")
#: `backfill_runs.kind` — why this run exists (data-model.md §5).
BACKFILL_KINDS: tuple[str, ...] = ("initial", "reconcile", "manual")


class BackfillRun(Base):
    """One resumable page-walk of one `(organization, entity)` pair (ADR-0020 §3,
    data-model.md §5). `app/backfill.py` is the only writer.

    Entities are a loose, regex-checked vocabulary rather than a fixed enum on purpose: I2-I9
    each register one more (`finance.invoices`, `deal.deals`, ...) without a migration to widen
    a CHECK constraint every time — the closed vocabulary that matters, which fact table a
    given `entity` string feeds, lives in `app/backfill.py`'s own registry, reviewable in a
    normal diff.
    """

    __tablename__ = "backfill_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="initial")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    #: `{"after_updated_at": "...", "after_id": "..."}`, advanced in the same transaction as the
    #: page's upserts (data-model.md §1.1) — a crash resumes from the last page that committed.
    cursor: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    rows_upserted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    rows_swept: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Transport and status only, never a response body (CLAUDE.md: no secrets or personal
    #: data in a log or a stored error).
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("entity ~ '^[a-z][a-z0-9_]*\\.[a-z][a-z0-9_]*$'", name="entity_shape"),
        CheckConstraint(f"kind in {BACKFILL_KINDS!r}", name="kind_vocabulary"),
        CheckConstraint(f"status in {BACKFILL_STATUSES!r}", name="status_vocabulary"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index(
            "ix_backfill_runs_org_entity_finished_at",
            "organization_id",
            "entity",
            "finished_at",
        ),
        # The one index that does not lead with organization_id (data-model.md §5, ADR-0015
        # §3): cross-tenant discovery on the BYPASSRLS `opsgrids_outbox` role has no tenant to
        # bind while it asks "what is due", the same shape as approval's SLA-sweep index.
        Index("ix_backfill_runs_status_next_attempt", "status", "next_attempt_at"),
    )


class FactMixin:
    """The columns every `*_facts` table carries on top of its own (data-model.md §1: "Fact
    columns"). Not a `Base` mixin on its own — each fact table still declares its own
    `organization_id`/`id` primary key, because `(organization_id, id)` is the source row's own
    UUID, never a surrogate.
    """

    #: The source row's `created_at`, or the payload's `created_at` where the source can backdate
    #: it. Otherwise the envelope's `occurred_at` on the creating event — the same instant.
    source_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: **The event version guard**: the envelope `occurred_at` for events, the source row's own
    #: `updated_at` for exports (§1.1). Every write here is `apply iff incoming.source_updated_at
    #: >= stored.source_updated_at` (events) or `apply iff stored.ingested_at < page_requested_at`
    #: (exports) — never a plain overwrite.
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Set from a delete event, a soft-deleted export row, or mark-and-sweep. Every read filters
    #: `deleted_at IS NULL` — not enforced by this mixin, because a read that wants deleted rows
    #: (mark-and-sweep itself) legitimately does not.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The last `backfill_runs.id` that exported this row, for mark-and-sweep (§1.1).
    last_seen_run: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: When insights last wrote the row. Load-bearing: the export overwrite rule and
    #: mark-and-sweep both compare against this, never against `source_updated_at`.
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class InvoiceFacts(Base, FactMixin):
    """Finance invoices and credit notes (data-model.md §3; ADR-0020 slice I2).

    Keyed `(organization_id, id)` with `id` the source `finance.invoices.id` — the same UUID in
    both schemas. Folded from `finance.billing.invoice.issued`/`.paid`
    (`app/consumers.py`) and from finance's own `GET /internal/v1/insights/export/invoices`
    (`app/backfill.py`).
    """

    __tablename__ = "invoice_facts"

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(Text, nullable=False)
    #: invoice | credit_note.
    document_type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: draft | sent | paid | overdue | void.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Business date, never converted (ADR-0020 §8).
    issued_date: Mapped[date] = mapped_column(SADate, nullable=False)
    due_date: Mapped[date] = mapped_column(SADate, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    #: Negative on a credit note. Kept alongside `total_minor` so revenue can move to net of tax
    #: without a backfill (ADR-0020 §9, open question Q1).
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Finance's own customer id. Not used for scope — finance is ORG/ORG (ADR-0020 §4).
    customer_ref: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: When the settling payment was recorded (`invoice.paid`'s `occurred_at`; the export's own
    #: `paid_at`). Null until the invoice is paid.
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "document_type in ('invoice', 'credit_note')", name="document_type_vocabulary"
        ),
        CheckConstraint(
            "status in ('draft', 'sent', 'paid', 'overdue', 'void')",
            name="status_vocabulary",
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_shape"),
        Index(
            "ix_invoice_facts_org_issued_date",
            "organization_id",
            "issued_date",
            postgresql_where=text("status not in ('draft', 'void')"),
        ),
        Index(
            "ix_invoice_facts_org_source_created",
            "organization_id",
            "source_created_at",
        ),
        Index(
            "ix_invoice_facts_org_paid_at",
            "organization_id",
            "paid_at",
            postgresql_where=text("paid_at is not null"),
        ),
    )


class PaymentFacts(Base, FactMixin):
    """Finance payments — append-only at the source, so a fact is written once and only a replay
    touches it again (data-model.md §3; ADR-0020 slice I2)."""

    __tablename__ = "payment_facts"

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    received_date: Mapped[date] = mapped_column(SADate, nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Derived by finance from the allocated invoices — `payments` carries no currency column of
    #: its own (events.md §5 finding 1).
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_shape"),
        Index("ix_payment_facts_org_received_date", "organization_id", "received_date"),
        Index(
            "ix_payment_facts_org_source_created",
            "organization_id",
            "source_created_at",
        ),
    )


class PurchaseFacts(Base, FactMixin):
    """Finance's purchase book (data-model.md §3; ADR-0020 slice I2). "Purchases recorded" /
    "Expenses" on the Finance dashboard sums `total_minor` here, signed by `document_type`."""

    __tablename__ = "purchase_facts"

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(Text, nullable=False)
    #: invoice | credit_note | debit_note.
    document_type: Mapped[str] = mapped_column(String(20), nullable=False)
    document_date: Mapped[date] = mapped_column(SADate, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    taxable_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "document_type in ('invoice', 'credit_note', 'debit_note')",
            name="document_type_vocabulary",
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_shape"),
        Index("ix_purchase_facts_org_document_date", "organization_id", "document_date"),
    )
