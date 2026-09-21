"""Tables owned by the itsm service — the IT service desk and CMDB.

Everything here lives in the `itsm` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). Links to people are plain UUIDs (`requester_id`, `assignee_id`,
`owner_id`) — never a constraint that crosses a schema boundary. Links **within** this
schema (`incidents.affected_asset_id`, `incident_worklogs.incident_id`) are real foreign
keys, because only cross-schema foreign keys are banned.

Four concerns, one schema:

* **incidents** — the ticket, with an ITIL priority (P1-P4), a lifecycle status and a real
  SLA clock (`sla_state`, `sla_due_at`, `sla_breached`, `sla_minutes_remaining`). SLA and
  priority are stored fields the UI labels, never colour alone.
* **incident_worklogs** — the append-only activity thread on an incident. A database trigger
  refuses UPDATE and DELETE (see the RLS migration), so the thread is a record, not a draft.
* **service requests / catalog** — the joiners-movers-leavers ("JML") flows and everyday
  fulfilment. A request points at a `catalog_items` offering by its human code.
* **assets** — the CMDB. Cost is integer minor units in an ISO currency (ADR-0000 §8), never
  a float. An incident may name the asset it affects.

The human identifiers the SPA speaks in — `INC-4821`, `REQ-2051`, `CAT-01`, `AST-1001` — are
stored as their own unique-per-org columns (`number` / `code` / `tag`); the primary key is an
opaque UUID, and the API exposes the human id.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "itsm"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Asset(Base, TenantBase):
    """One configuration item in the CMDB.

    `tag` (`AST-1001`) is the human identity the service desk quotes and the API keys on;
    it is unique within an organization, not globally. `owner_id` is a plain UUID into the
    account directory — the scope column for OWN readings — while `owner` is the display name
    the SPA shows, kept denormalised so a row renders without a directory lookup.

    Cost is `cost_minor` (integer minor units) plus `cost_currency` (ISO-4217). No float ever
    touches money (ADR-0000 §8).
    """

    __tablename__ = "assets"

    tag: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: laptop | desktop | server | monitor | phone | network | license.
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: in_use | in_stock | in_repair | retired.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="in_stock")

    #: Directory id of the owner, for OWN scope. A plain UUID, never a cross-schema FK.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: Display name of the owner (or a team name). Denormalised for the table view.
    owner: Mapped[str | None] = mapped_column(String(120))
    location: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    serial: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    purchased_at: Mapped[date | None] = mapped_column(Date)
    #: Warranty / support expiry — drives the "expiring soon" flag in the UI.
    warranty_expires_at: Mapped[date | None] = mapped_column(Date)

    cost_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    __table_args__ = (
        # Every index leads with `organization_id`: under row-level security the policy qual is
        # ANDed into every query, so an index that did not lead with the tenant column would
        # scan the whole platform's rows and discard almost all of them (ADR-0002).
        UniqueConstraint("organization_id", "tag", name="uq_assets_org_tag"),
        Index("ix_assets_org_status", "organization_id", "status"),
        Index("ix_assets_org_type", "organization_id", "asset_type"),
        Index("ix_assets_org_owner", "organization_id", "owner_id"),
    )


class Incident(Base, TenantBase):
    """A reported disruption or request-for-help — the ITSM ticket.

    `number` (`INC-4821`) is the human identity, unique within an organization. `requester_id`
    and `assignee_id` are plain directory UUIDs used for scope (OWN reads the incidents a
    caller raised); `requester` and `assignee` are the display names the SPA renders.

    Priority and the SLA clock are first-class stored fields — `priority` (P1-P4), `sla_state`
    (met / on_track / at_risk / breached), `sla_due_at`, `sla_breached` and the convenience
    `sla_minutes_remaining` — so the UI labels them in words and never leans on colour alone.
    """

    __tablename__ = "incidents"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: P1 | P2 | P3 | P4 — ITIL urgency band, P1 the most severe.
    priority: Mapped[str] = mapped_column(String(2), nullable=False, default="P3")
    #: new | in_progress | on_hold | resolved | closed.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    #: network | hardware | software | access | email | security.
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="software")

    requester_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    requester: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    assignee: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    #: The CMDB asset this incident is against, if any. A real FK — same schema, so allowed —
    #: with the row set NULL if the asset is ever removed.
    affected_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("itsm.assets.id", ondelete="SET NULL")
    )

    #: SLA clock state, always surfaced with a text label: met | on_track | at_risk | breached.
    sla_state: Mapped[str] = mapped_column(String(20), nullable=False, default="on_track")
    #: When the SLA target falls due. Real timestamp; NULL once met/closed.
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Whether the target has already been missed — a stored flag, not a colour.
    sla_breached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Minutes remaining on the clock; negative once breached; NULL once met/closed.
    sla_minutes_remaining: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_incidents_org_number"),
        Index("ix_incidents_org_status", "organization_id", "status"),
        Index("ix_incidents_org_priority", "organization_id", "priority"),
        Index("ix_incidents_org_category", "organization_id", "category"),
        Index("ix_incidents_org_asset", "organization_id", "affected_asset_id"),
        Index("ix_incidents_org_requester", "organization_id", "requester_id"),
        Index("ix_incidents_org_updated", "organization_id", "updated_at"),
    )


class IncidentWorklog(Base, TenantBase):
    """One entry in an incident's activity thread — **append-only**.

    Appended when an incident is raised, noted on, reassigned, breaches SLA or is resolved.
    A database trigger (see the RLS migration) refuses UPDATE and DELETE, so a worklog is a
    record of what happened, not a document that can be quietly rewritten. There is no service
    or route that mutates one by design, and the trigger makes that a guarantee rather than a
    convention.
    """

    __tablename__ = "incident_worklogs"

    #: The incident this entry belongs to. A real FK — same schema — and the row is removed
    #: with its incident.
    incident_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("itsm.incidents.id", ondelete="CASCADE"),
        nullable=False,
    )
    author: Mapped[str] = mapped_column(String(120), nullable=False)
    #: A human sentence describing the event. Named `body` to match the SPA's `WorklogEntry`.
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: note | status | assignment | sla | resolution.
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="note")
    #: When the event happened — carried explicitly so a backfilled thread keeps real times.
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_incident_worklogs_org_incident", "organization_id", "incident_id", "at"),
    )


class CatalogItem(Base, TenantBase):
    """One offering in the service catalog — the request types.

    `code` (`CAT-01`) is the human identity a request points at, unique within an organization.
    Tenant-scoped rather than a global reference table: `requests_30d` is a per-organization
    rollup and every table carrying tenant-flavoured data takes `organization_id` + RLS.
    """

    __tablename__ = "catalog_items"

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    #: onboarding | offboarding | access | hardware | software.
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Target fulfilment time in working days.
    sla_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: How many of this item were requested in the trailing 30 days (sample rollup).
    requests_30d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_catalog_items_org_code"),
        Index("ix_catalog_items_org_category", "organization_id", "category"),
    )


class ServiceRequest(Base, TenantBase):
    """A service request raised against the catalog — the JML and fulfilment flows.

    `number` (`REQ-2051`) is the human identity. `catalog_item_code` names the offering by its
    `CatalogItem.code` (a plain in-schema reference, validated in the service rather than by a
    foreign key, because the code is a natural key and the join is denormalised for the table).
    `requester_id` is the directory UUID used for OWN scope; `requester` / `subject` are display.
    """

    __tablename__ = "requests"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    catalog_item_code: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Denormalised from the catalog item so a request row renders without a second lookup.
    category: Mapped[str] = mapped_column(String(20), nullable=False)

    requester_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    requester: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Who the request is for — often the requester, but not for JML flows.
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    #: approval | in_progress | fulfilled | rejected.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="approval")
    #: Who holds the next step, while awaiting approval / in progress. NULL once fulfilled.
    assignee: Mapped[str | None] = mapped_column(String(120))
    #: Why a request was rejected (or any other decision note). NULL until a decision is made —
    #: a rejection with no visible reason is not usable, so this rides the decision endpoint.
    decision_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_requests_org_number"),
        Index("ix_requests_org_status", "organization_id", "status"),
        Index("ix_requests_org_catalog", "organization_id", "catalog_item_code"),
        Index("ix_requests_org_requester", "organization_id", "requester_id"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
