import type { CompanyStatus, ContactStatus, DealStage } from "@/api/types";
import { stageLabel } from "@/data/stages";
import { cn } from "@opsgrids/ui";

const PILL_BASE =
  "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold whitespace-nowrap";

/* --------------------------------- company -------------------------------- */

const COMPANY_TONE: Record<CompanyStatus, string> = {
  customer: "bg-present-soft text-present",
  prospect: "bg-info-soft text-info",
  lead: "bg-cyan-soft text-cyan",
  churned: "bg-ground text-muted",
};

const COMPANY_LABEL: Record<CompanyStatus, string> = {
  customer: "Customer",
  prospect: "Prospect",
  lead: "Lead",
  churned: "Churned",
};

/** A company's relationship status. A dot backs up the colour so it never rests on hue alone. */
export function CompanyStatusPill({ status }: { status: CompanyStatus }) {
  return (
    <span className={cn(PILL_BASE, COMPANY_TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {COMPANY_LABEL[status]}
    </span>
  );
}

/* --------------------------------- contact -------------------------------- */

const CONTACT_TONE: Record<ContactStatus, string> = {
  customer: "bg-present-soft text-present",
  qualified: "bg-info-soft text-info",
  lead: "bg-cyan-soft text-cyan",
  dormant: "bg-ground text-muted",
};

const CONTACT_LABEL: Record<ContactStatus, string> = {
  customer: "Customer",
  qualified: "Qualified",
  lead: "Lead",
  dormant: "Dormant",
};

export function ContactStatusPill({ status }: { status: ContactStatus }) {
  return (
    <span className={cn(PILL_BASE, CONTACT_TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {CONTACT_LABEL[status]}
    </span>
  );
}

/* ---------------------------------- stage --------------------------------- */

const STAGE_TONE: Record<DealStage, string> = {
  lead: "bg-ground text-muted",
  qualified: "bg-info-soft text-info",
  proposal: "bg-accent-soft text-accent-ink",
  negotiation: "bg-late-soft text-late",
  closed_won: "bg-present-soft text-present",
  closed_lost: "bg-danger-soft text-danger",
};

/** A deal's pipeline stage as a pill. */
export function StagePill({ stage }: { stage: DealStage }) {
  return (
    <span className={cn(PILL_BASE, STAGE_TONE[stage])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {stageLabel(stage)}
    </span>
  );
}
