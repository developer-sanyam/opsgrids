"""Cold e-mail to CRM leads (ADR-0038 addendum 2026-10-16).

B2B outreach under legitimate interest, from the CRM Leads list. The flow here is *pull at send
time*: the rep's selection is read from lead (`crm_leads.fetch_leads`, scope applied there), the
leads with an address are copied into the contact book (`origin = 'crm_lead'`), and a draft
campaign with `cold_outreach` on is created over a list of exactly those contacts. Sending is the
ordinary campaign path (`request_send` / `schedule_send` and every gate in it).

**What the flag changes, and what it does not.** A cold campaign needs "not opted out" where an
ordinary one needs "opted in", and only for a contact carrying a `crm_lead_id`. Nothing here
writes a consent event or opts anyone in. Suppressions (bounce, complaint, unsubscribe), a STOP or
an unsubscribe, the verified sender, the unsubscribe link and `List-Unsubscribe` headers all apply
exactly as to any campaign, and `Settings.engage_cold_daily_cap` bounds an organization's cold mail
per UTC day (enforced by the send driver; the rest stays queued for the next day).

Counts, logs and audit rows hold numbers only: never a name, an address or a message.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from fastapi import Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crm_leads, domain, selectors, services
from app.config import Settings, settings
from app.crm_leads import LeadRow, LeadsUnavailable, TooManyLeads
from app.models import AuditLog, Campaign, CampaignRecipient, Contact
from app.schemas import (
    LeadAudienceCounts,
    LeadAudiencePreviewIn,
    LeadAudiencePreviewOut,
    LeadCampaignIn,
    LeadColdEmailOut,
    LeadSample,
    LeadSelectionIn,
)
from platform_core.audit import record_audit
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.cache import enforce_rate_limit
from platform_core.errors import (
    BusinessRuleProblem,
    ForbiddenProblem,
    NotFoundProblem,
    PaymentRequiredProblem,
    UpstreamUnavailableProblem,
)

LEAD_READ: Final = "crm.lead.lead.read"
#: Leads in these states are customers or closed; they are never cold-mailed.
SKIPPED_STATUSES: Final = frozenset({"converted", "disqualified"})
#: The attribute keys a lead copy carries, which `{{lead_status}}` and friends resolve to.
LEAD_ATTRIBUTES: Final = frozenset({"lead_status", "lead_source", "company_name"})


# ----------------------------------------------------------------------------- authorization


def require_lead_access(principal: Principal) -> uuid.UUID | None:
    """Both entitlements and the caller's lead-read scope, as the `owner_id` lead must apply.

    Returns None for an ORG reader and the caller's own rep id for an OWN reader. TEAM and
    LOCATION do not exist on leads (the CRM lead resolver refuses them) and are refused here too.
    The route has already required the Engage entitlement and `engage.campaign.campaign.write`.
    """
    if not principal.has_product("crm"):
        raise PaymentRequiredProblem("crm is not active for this organization")
    scope = principal.scope_for(LEAD_READ)
    if scope is None:
        raise ForbiddenProblem(f"Missing permission: {LEAD_READ}")
    if scope is Scope.ORG:
        return None
    if scope is Scope.OWN and principal.subject_ref is not None:
        return principal.subject_ref
    raise ForbiddenProblem("Your lead access does not allow emailing leads")


async def read_selection(
    principal: Principal, selection: LeadSelectionIn, *, emails: bool = True
) -> list[LeadRow]:
    """The selected leads the caller may read, from lead. 503 if lead cannot answer; 422 if the
    selection is larger than one campaign may target."""
    owner_id = require_lead_access(principal)
    cfg: Settings = settings()
    # Each read opens leads' addresses in lead and is audited there, so it is bounded per person.
    await enforce_rate_limit(
        f"engage.lead_audience:{principal.organization_id}:{principal.sub}",
        limit=cfg.engage_lead_audience_rate_limit_per_minute,
        window_seconds=60,
    )
    try:
        return await crm_leads.fetch_leads(
            cfg,
            principal.organization_id,
            ids=selection.lead_ids,
            status=selection.status,
            source=selection.source,
            owner_id=owner_id,
            emails=emails,
        )
    except TooManyLeads:
        raise BusinessRuleProblem(
            f"That is more than {cfg.engage_cold_audience_max} leads. Narrow the filter."
        ) from None
    except LeadsUnavailable:
        raise UpstreamUnavailableProblem("Leads could not be read right now. Try again.") from None


# ----------------------------------------------------------------------------- classification


@dataclass(slots=True)
class _Mailable:
    lead: LeadRow
    email: str
    sealed: bytes
    masked: str
    index: str


@dataclass(slots=True)
class Classified:
    selected: int
    with_email: int
    skipped_status: int
    excluded: int
    #: Leads that pass every rule, with the address they will be mailed on.
    sendable: list[_Mailable]


async def classify(
    session: AsyncSession, organization_id: uuid.UUID, leads: list[LeadRow]
) -> Classified:
    """Count a selection the way the send path will treat it. Two batch reads for the whole
    selection (suppressions, then the consent ledger); no per-lead query."""
    with_email: list[_Mailable] = []
    for lead in leads:
        if not lead.email:
            continue
        try:
            address = domain.normalize_recipient("email", lead.email)
        except domain.RuleError:
            continue
        sealed, masked, index = services._sealed_email(address)
        with_email.append(_Mailable(lead, address, sealed, masked, index))
    skipped = [m for m in with_email if m.lead.status in SKIPPED_STATUSES]
    candidates = [m for m in with_email if m.lead.status not in SKIPPED_STATUSES]
    indexes = [m.index for m in candidates]
    blocked = await selectors.suppressed_indexes(session, organization_id, indexes)
    ledger = await selectors.latest_consent_status(
        session, organization_id, "email", "marketing", indexes
    )
    blocked |= {i for i, status in ledger.items() if status == "opted_out"}
    mailable = [m for m in candidates if m.index not in blocked]
    return Classified(
        selected=len(leads),
        with_email=len(with_email),
        skipped_status=len(skipped),
        excluded=len(candidates) - len(mailable),
        sendable=mailable,
    )


async def _counts(
    session: AsyncSession, organization_id: uuid.UUID, found: Classified
) -> LeadAudienceCounts:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return LeadAudienceCounts(
        selected=found.selected,
        with_email=found.with_email,
        skipped_status=found.skipped_status,
        excluded=found.excluded,
        sendable=len(found.sendable),
        daily_cap=settings().engage_cold_daily_cap,
        sent_today=await selectors.cold_mail_sent_since(session, organization_id, today),
    )


def _lead_values(lead: LeadRow) -> dict[str, Any]:
    """Merge values for a lead, as the sent copy will have them (`services._merge_values` over a
    contact built by `_upsert_contacts`). The address is never part of a sample."""
    first, _, last = lead.full_name.strip().partition(" ")
    company = (lead.company or "").strip()
    values = {
        "first_name": first, "last_name": last.strip(), "name": lead.full_name.strip(),
        "full_name": lead.full_name.strip(), "customer_name": lead.full_name.strip(),
        "company_name": company, "company": company,
        "lead_status": lead.status, "lead_source": lead.source,
    }  # fmt: skip
    return {k: v for k, v in values.items() if v}


async def preview(
    session: AsyncSession,
    principal: Principal,
    body: LeadAudiencePreviewIn,
    leads: list[LeadRow],
) -> LeadAudiencePreviewOut:
    """Counts, cap, and the draft filled in with the first lead that can be mailed. Writes
    nothing."""
    found = await classify(session, principal.organization_id, leads)
    counts = await _counts(session, principal.organization_id, found)
    first = found.sendable[0].lead if found.sendable else None
    used = domain.merge_names(body.subject + body.text_body)
    known = domain.MERGE_BUILTINS | LEAD_ATTRIBUTES
    unknown = used - known
    if unknown:
        unknown -= await selectors.contact_attribute_keys(session, principal.organization_id)
    sample = None
    if first is not None and (body.subject or body.text_body):
        values = _lead_values(first)
        sample = LeadSample(
            lead_id=str(first.lead_id),
            full_name=first.full_name,
            subject=domain.merge_fields(body.subject, values),
            text_body=domain.merge_fields(body.text_body, values),
        )
    return LeadAudiencePreviewOut(
        **counts.model_dump(), sample=sample, unknown_fields=sorted(unknown)
    )


# ----------------------------------------------------------------------------- the copy


async def _upsert_contacts(
    session: AsyncSession, organization_id: uuid.UUID, actor: uuid.UUID, mailable: list[_Mailable]
) -> tuple[list[uuid.UUID], int]:
    """Make a contact for each mailable lead; returns `(contact ids, leads skipped)`.

    Keyed on the address's blind index (a lead's email is unique among live leads): a contact
    already holding it is linked to the lead (never duplicated, its origin and consent history
    untouched); otherwise a `crm_lead` row is created. A contact that held the lead's id under an
    *older* address is released first (`crm_lead_id` is unique per organization), and one whose
    row is soft-deleted is skipped. Two batch reads, bulk writes.
    """
    if not mailable:
        return [], 0
    lead_ids = [m.lead.lead_id for m in mailable]
    indexes = [m.index for m in mailable]
    rows = list(
        (
            await session.execute(
                select(Contact).where(
                    Contact.organization_id == organization_id,
                    or_(Contact.crm_lead_id.in_(lead_ids), Contact.email_index.in_(indexes)),
                )
            )
        ).scalars()
    )
    by_index = {c.email_index: c for c in rows if c.email_index}
    by_lead = {c.crm_lead_id: c for c in rows if c.crm_lead_id}
    for m in mailable:
        held = by_lead.get(m.lead.lead_id)
        if held is not None and held.email_index != m.index:
            held.crm_lead_id = None
    # Released ids and relinked rows must be written before any row takes the id again.
    await session.flush()

    linked: list[Contact] = []
    skipped = 0
    for m in mailable:
        contact = by_index.get(m.index)
        if contact is not None and contact.deleted_at is not None:
            skipped += 1
            continue
        if contact is None:
            contact = Contact(
                organization_id=organization_id,
                created_by=str(actor),
                email_enc=m.sealed,
                email_masked=m.masked,
                email_index=m.index,
                origin="crm_lead",
                attributes={},
            )
            contact.name = m.lead.full_name[:200]
            session.add(contact)
        # An address linked to another lead id (the lead was recreated) is simply relinked.
        contact.crm_lead_id = m.lead.lead_id
        if not contact.name:
            contact.name = m.lead.full_name[:200]
        contact.attributes = {
            **contact.attributes,
            "lead_status": m.lead.status,
            "lead_source": m.lead.source,
            **(
                {"company_name": m.lead.company}
                if m.lead.company and not contact.attributes.get("company_name")
                else {}
            ),
        }
        by_index[m.index] = contact
        linked.append(contact)
    await session.flush()
    return [c.id for c in linked], skipped


async def create_lead_campaign(
    session: AsyncSession,
    ctx: ScopeContext,
    body: LeadCampaignIn,
    leads: list[LeadRow],
    *,
    request: Request | None = None,
) -> tuple[Campaign, Classified, LeadAudienceCounts]:
    """The contacts, a list of exactly them, and a draft cold-outreach campaign over that list.
    One transaction: a refused sender or list leaves nothing behind."""
    found = await classify(session, ctx.organization_id, leads)
    if not found.sendable:
        raise BusinessRuleProblem(
            "None of the selected leads can be emailed: they have no address, are converted "
            "or disqualified, or have unsubscribed."
        )
    contact_ids, _ = await _upsert_contacts(
        session, ctx.organization_id, ctx.principal.sub, found.sendable
    )
    name = (body.name.strip() or f"Cold email {datetime.now(UTC):%Y-%m-%d %H:%M}")[:160]
    audience = await services.create_list(
        session, ctx, name=f"{name} ({uuid.uuid4().hex[:6]})", description="Leads, cold email"
    )
    # Every mailable lead's contact joins, including one a later opt-out will drop at send time.
    await services.change_list_members(
        session, ctx, list_id=audience.id, contact_ids=contact_ids, add=True
    )
    campaign = await services.create_campaign(
        session,
        ctx,
        fields={
            "name": name,
            "channel": "email",
            "sender_id": body.sender_id,
            "subject": body.subject,
            "text_body": body.text_body,
            "html_body": body.html_body,
            "audience": "list",
            "list_id": audience.id,
            "cold_outreach": True,
        },
        request=request,
    )
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.lead_campaign.created",
        entity="campaign",
        entity_id=campaign.id,
        after={
            "selected": found.selected,
            "with_email": found.with_email,
            "excluded": found.excluded,
            "skipped_status": found.skipped_status,
            "sendable": len(found.sendable),
        },
        request=request,
    )
    return campaign, found, await _counts(session, ctx.organization_id, found)


# ----------------------------------------------------------------------------- timeline


async def lead_cold_emails(
    session: AsyncSession, organization_id: uuid.UUID, lead_id: uuid.UUID, *, limit: int = 20
) -> list[LeadColdEmailOut]:
    """ "Cold email sent: <subject>" lines for one lead, newest first, one query. The subject is
    the campaign's, filled in for this lead; no body and no address ever appear."""
    stmt = (
        select(Campaign, CampaignRecipient.sent_at, Contact)
        .join(CampaignRecipient, CampaignRecipient.campaign_id == Campaign.id)
        .join(Contact, Contact.id == CampaignRecipient.contact_id)
        .where(
            Campaign.organization_id == organization_id,
            CampaignRecipient.organization_id == organization_id,
            Contact.organization_id == organization_id,
            Contact.crm_lead_id == lead_id,
            Campaign.cold_outreach.is_(True),
            CampaignRecipient.status == "sent",
            CampaignRecipient.sent_at.is_not(None),
        )
        .order_by(CampaignRecipient.sent_at.desc())
        .limit(limit)
    )
    out: list[LeadColdEmailOut] = []
    for campaign, sent_at, contact in (await session.execute(stmt)).all():
        subject = domain.merge_fields(campaign.subject, services._merge_values(contact, ""))
        out.append(
            LeadColdEmailOut(
                campaign_id=str(campaign.id), title=f"Cold email sent: {subject}", at=sent_at
            )
        )
    return out


async def lead_visible(principal: Principal, lead_id: uuid.UUID) -> None:
    """404 unless the lead exists and the caller's scope reaches it. Opens no address."""
    leads = await read_selection(principal, LeadSelectionIn(lead_ids=[lead_id]), emails=False)
    if not leads:
        raise NotFoundProblem("No such lead")
