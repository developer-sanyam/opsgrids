import type { CompanyStatus, ContactStatus, DealStage, LeadStatus } from "@/api/types";
import { stageLabel } from "@/data/stages";
import { StatusBadge } from "@opsgrids/ui";
import type { StatusTone } from "@opsgrids/ui";

/*
 * Thin wrappers over the shared `StatusBadge` (opsgrids-ui's status registry) so the same word
 * carries the same colour in every app. Words the registry does not know take an explicit tone
 * override rather than a sixth colour — listed here:
 *   - CompanyStatus/ContactStatus "prospect"/"lead" -> info, "customer" -> success
 *   - ContactStatus "qualified" -> info
 *   - DealStage "lead"/"qualified"/"proposal" -> info, "negotiation" -> warning,
 *     "closed_won" -> success, "closed_lost" -> danger
 *   - LeadStatus "working" -> info, "qualified" -> warning
 */

/* --------------------------------- company -------------------------------- */

const COMPANY_TONE: Record<CompanyStatus, StatusTone> = {
  customer: "success",
  prospect: "info",
  lead: "info",
  churned: "neutral",
};

const COMPANY_LABEL: Record<CompanyStatus, string> = {
  customer: "Customer",
  prospect: "Prospect",
  lead: "Lead",
  churned: "Churned",
};

/** A company's relationship status. */
export function CompanyStatusPill({ status }: { status: CompanyStatus }) {
  return <StatusBadge tone={COMPANY_TONE[status]} label={COMPANY_LABEL[status]} />;
}

/* --------------------------------- contact -------------------------------- */

const CONTACT_TONE: Record<ContactStatus, StatusTone> = {
  customer: "success",
  qualified: "info",
  lead: "info",
  dormant: "neutral",
};

const CONTACT_LABEL: Record<ContactStatus, string> = {
  customer: "Customer",
  qualified: "Qualified",
  lead: "Lead",
  dormant: "Dormant",
};

/** A contact's relationship status. */
export function ContactStatusPill({ status }: { status: ContactStatus }) {
  return <StatusBadge tone={CONTACT_TONE[status]} label={CONTACT_LABEL[status]} />;
}

/* ---------------------------------- stage --------------------------------- */

const STAGE_TONE: Record<DealStage, StatusTone> = {
  lead: "info",
  qualified: "info",
  proposal: "info",
  negotiation: "warning",
  closed_won: "success",
  closed_lost: "danger",
};

/** A deal's pipeline stage as a pill. */
export function StagePill({ stage }: { stage: DealStage }) {
  return <StatusBadge tone={STAGE_TONE[stage]} label={stageLabel(stage)} />;
}

/* ---------------------------------- lead ---------------------------------- */

const LEAD_LABEL: Record<LeadStatus, string> = {
  new: "New",
  working: "Working",
  qualified: "Qualified",
  disqualified: "Disqualified",
  converted: "Converted",
};

/** "new", "disqualified" and "converted" are registered words; "working"/"qualified" are not. */
const LEAD_TONE: Record<LeadStatus, StatusTone> = {
  new: "info",
  working: "info",
  qualified: "warning",
  disqualified: "neutral",
  converted: "success",
};

/** A lead's place in the intake lifecycle. */
export function LeadStatusPill({ status }: { status: LeadStatus }) {
  return <StatusBadge tone={LEAD_TONE[status]} label={LEAD_LABEL[status]} />;
}
