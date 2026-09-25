"""Tables owned by the document service.

Everything here lives in the `document` schema and nothing outside it may hold a foreign
key into it (ADR-0000 §10).

**What this service is.** ADR-0009 §2 makes `document` the platform's **public asset
store** for images, and its 2026-09-24 addendum (Q1) adds a **private** half alongside it:
`private_objects`, backed by a bucket with no anonymous policy at all. The two never share a
table or a bucket — a private object is never in `assets` and never in the public bucket, and
an asset is never presigned. `assets` carries no reference count and never will: the document
that would reference an image lives in a service that does not exist, and when it does it
will own a different schema that ADR-0000 §10 forbids reading across. A reference check here
is impossible by construction rather than merely expensive, which is what drives the deletion
design in `app/services.py` (ADR-0009 §8).

`private_objects` is deliberately simpler: no soft-delete grace window (ADR-0009 §8 exists
because a *public* URL might already be embedded in a merchant's live store; a private object
that has not yet been attached to anything has no such audience), and no re-encoding (a
payslip PDF is not decoded and re-written the way an image is). What it keeps from the public
design: the object is written before the row (`app/services.py`), the row is the only thing
any route ever reaches by id, and the key is never accepted from a caller.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, String, desc
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "document"

#: The three formats this service will decode and store (ADR-0009 §5). SVG is refused
#: permanently — it is a script container, and a direct navigation to an object URL executes
#: it on a host we control and will later put a cookie-bearing CDN domain in front of. GIF is
#: refused for now, because it is usually animated and the re-encode would silently flatten
#: it to one frame; refusing is louder than ruining.
ACCEPTED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Asset(Base, TenantBase):
    """One merchant-uploaded image, stored world-readable at an unguessable key.

    `TenantBase` supplies `id`, `organization_id`, `created_at`, `updated_at` and
    `deleted_at`. `id` is the asset id that appears in the object key, so a key collision is
    a primary-key collision — impossible by constraint rather than improbable by entropy,
    which is strictly better than ADR-0009 §10's "122 bits makes it not happen".

    **Soft delete is not filtered automatically** (`SoftDeleteMixin`'s own rule). Every query
    in `app/selectors.py` adds the predicate itself, and the purge sweep depends on being
    able to see soft-deleted rows.
    """

    __tablename__ = "assets"

    #: `{organization_id}/{asset_id}`. Derivable from the other two columns and stored
    #: anyway: ADR-0009 §12 keeps it after purge so an object found in a bucket during an
    #: incident can be tied back to a row without reconstructing the format from memory.
    #:
    #: **Never accepted from a caller.** Every route addresses an asset by row id; the row is
    #: fetched under RLS and the key is read off it. See `app/api/routes.py`.
    storage_key: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Derived from what Pillow decoded, never from what the request claimed (ADR-0009 §5).
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Of the **stored** object, after re-encode. This is what the quota counts and what a
    #: future `document.asset.bytes_added` meter would be reconstructed from.
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Post-downscale. The editor uses them to reserve space and avoid layout shift.
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Of the stored bytes. Integrity checks, and a possible future dedupe — whose index is
    #: deliberately **rejected** rather than deferred (ADR-0009 §12).
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Display only, in the picker. Never used to build a key or a path, and sanitised at
    #: render rather than at rest.
    original_filename: Mapped[str | None] = mapped_column(String(255))
    #: Who uploaded it. For the picker and for audit context — **not** a scope input: both
    #: asset permissions are ORG-only, so there is no "own uploads" reading to support.
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: Set by `?purge=true` or by the retention sweep. **The row survives**: the usage history
    #: this table holds is what makes deferring metering safe, and a sweep that hard-deleted
    #: would destroy it 30 days after the fact, quietly (ADR-0009 §7).
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "content_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="content_type",
        ),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        CheckConstraint("width > 0 AND height > 0", name="dimensions_positive"),
        # The library listing — `GET /assets`, the editor's picker. The only hot read, and the
        # only index this slice ships (ADR-0009 §12's index-timing register). Leads with
        # `organization_id` because an RLS qual is ANDed into every query: an index that does
        # not lead with the tenant column stops doing its job the moment policies are on.
        Index(
            "ix_assets_org_created_at_live",
            "organization_id",
            desc("created_at"),
            postgresql_where="deleted_at IS NULL",
        ),
        # The purge sweep's own cross-tenant discovery (ADR-0009 §8, §12) —
        # `app/selectors.py`'s `due_purge_assets`, run on the BYPASSRLS outbox role. Deliberately
        # **not** leading with `organization_id`: the query has no tenant to bind while asking
        # which organizations have assets due for purging, so an org-led index would not serve
        # it. Shipped in the sweep's own slice, not with the table — ADR-0009 §12's own rule.
        Index(
            "ix_assets_purge_sweep_discovery",
            "deleted_at",
            postgresql_where="deleted_at IS NOT NULL AND purged_at IS NULL",
        ),
    )


class PrivateObject(Base, TenantBase):
    """One private document — a payslip, a signed contract, an ID scan.

    `TenantBase` supplies `id`, `organization_id`, `created_at`, `updated_at`, `deleted_at`. The
    object key (`{organization_id}/{owner_service}/{id}`) uses this row's own primary key, the
    same "no key collision, ever" property `Asset` has (ADR-0009 §12).

    **Two-phase, and `status` says which phase a row is in.** `initiate` inserts a `pending` row
    and hands the caller a presigned upload — nothing has necessarily reached the bucket yet.
    `complete` re-reads the object directly from storage (never trusting what the client
    claims), and only then does the row become `stored` with its real `content_type`,
    `size_bytes` and `checksum_sha256` filled in. A `pending` row that is never completed is an
    upload that never happened as far as every other route is concerned — `get`, `download` and
    `delete` all refuse a `pending` row that has not been completed by its own uploader within
    the upload TTL. Nothing in this slice sweeps abandoned `pending` rows or the orphan objects
    a completed-then-abandoned presign could leave in the bucket; see the module's own note in
    `app/cron.py` for why that is named as follow-up rather than built here.

    **`owner_service`, not a foreign key.** ADR-0000 §10 forbids a cross-schema key, and the
    product that will eventually attach this object — payroll, a compliance filing — is not
    knowable from this schema. It is the first dotted segment of the caller-supplied `purpose`
    (`app/domain.py:parse_purpose`), so the key groups objects by the product that asked for
    them without this service holding an opinion about what products exist.

    **`owner_aggregate_id` and `attached_at` are write-once, from nowhere in this slice.** No
    route here sets them — attaching a completed upload to the record it belongs to is the
    owning product's own write, against its own schema, and is out of scope for this slice
    (see the service README). They exist now so that a future attach step is a column update,
    not a migration, and so `DELETE` has something concrete to refuse against once one exists:
    an attached object is never deleted through this API.
    """

    __tablename__ = "private_objects"

    #: `{organization_id}/{owner_service}/{id}`. Never accepted from a caller — read off the
    #: row after it is fetched under RLS, the same rule `assets.storage_key` follows.
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The caller-supplied purpose string verbatim (`app/domain.py:PURPOSE_PATTERN`), e.g.
    #: `"payroll.payslip"`. Its first segment is `owner_service`, stored separately below
    #: because the key and the audit trail need it as its own value, not re-parsed each time.
    purpose: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_service: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Set only by a future attach step. `None` for the whole of this slice.
    owner_aggregate_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: `pending` after `initiate`, `stored` after `complete` verifies the real bytes. No other
    #: values exist; a purge/expiry state is future work (see the class docstring).
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    #: The client's declared type at `initiate`. Kept for comparison against what `complete`
    #: actually finds — a mismatch is refused (415), never silently corrected.
    declared_content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Filled by `complete`, from the bytes themselves, never from what the client claims.
    content_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    #: Display only. Never used to build a key or a path.
    original_filename: Mapped[str | None] = mapped_column(String(255))
    #: Who, in human/audit terms — the platform user id (`ScopeContext.principal.sub`). Shown in
    #: a picker and written to the audit row. **Not** the OWN-scope filter column; see
    #: `owner_subject_ref` below for why the two must not be conflated.
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: `ScopeContext.principal.subject_ref` at the moment of `initiate` — the ADR-0008 employee
    #: link, the same column every other OWN-scoped product in this platform compares against
    #: (`itsm.Incident.requester_id`, `crm.Company.owner_id`, `leave.LeaveRequest.employee_id`).
    #: `app/selectors.py` passes this, not `uploaded_by_user_id`, as `ctx.apply`'s `owner_col`.
    #:
    #: **Inherits ADR-0008's own documented gap**: an organization with no HRMS activated links
    #: no membership to an employee, `subject_ref` is `None` on every token minted there, and
    #: every OWN grant on `core.content.private_document.*` is therefore permanently empty for
    #: that tenant — exactly the trade-off ADR-0008 names for `itsm`/`crm`/`timesheet`, not a
    #: defect specific to this table. `None` here reads as "not `subject_ref`-owned by anyone",
    #: never as "owned by everyone" — `ScopeContext.apply` already refuses to compare a column
    #: to a `None` subject rather than rendering `IS NULL`, so a caller with no employee link
    #: cannot see *any* private object at OWN, including their own.
    owner_subject_ref: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: The presigned upload's own deadline. `complete` refuses a `pending` row past this even
    #: if the object somehow made it to the bucket late — the TTL is the contract, not a hint.
    upload_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'stored')", name="status"),
        CheckConstraint("declared_size_bytes > 0", name="declared_size_bytes_positive"),
        # The uploader's own library, if this ever grows one — not shipped this slice, named so
        # the index arrives with the query that needs it rather than speculatively.
        Index(
            "ix_private_objects_org_owner_service_created_at",
            "organization_id",
            "owner_service",
            desc("created_at"),
        ),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe.

    Empty in this slice and deliberately so: ADR-0009 §7 refuses to publish
    `core.document.asset.uploaded`, because an event type with no consumer is a contract kept
    compatible forever in exchange for nothing (ADR-0008 §3's precedent).
    """


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
