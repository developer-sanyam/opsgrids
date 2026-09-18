"""Tables owned by the document service.

Everything here lives in the `document` schema and nothing outside it may hold a foreign
key into it (ADR-0000 §10).

**What this service is, stated where it is easiest to get wrong.** ADR-0009 §2 narrows
`document` to the platform's **public asset store**, not a document vault. Every object it
writes goes into a bucket that is world-readable in its entirety, and there is no way
through this API to store a private object. The private half — `S3_PRIVATE_BUCKET`, a
short-lived presigned GET behind an authorized route — is named in that ADR and deliberately
unbuilt, so the failure mode here is "the feature is missing", never "the payslip went
somewhere surprising". The service's *name* invites the opposite assumption; this paragraph
is one of the three places that is contradicted in words.

`assets` is the one domain table. `Asset` carries no reference count and never will: the
store document that references an image lives in a storefront service that does not exist,
and when it does it will own a different schema that ADR-0000 §10 forbids reading across. A
reference check here is impossible by construction rather than merely expensive, which is
what drives the deletion design in `app/services.py` (ADR-0009 §8).
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
