import type { ReactNode } from "react";

import { cn, TagIcon } from "@opsgrids/ui";

import type { AccountStatus, Invoice, LedgerKind, PlanVersionState } from "@/api/types";

/**
 * The lifecycle families (ADR-0004 §4), expressed over the Console's semantic tokens rather than
 * as a palette of their own: active is the green `present`, trial the blue `info`, past-due the
 * amber `late`, suspended the red `danger`, comp the `cyan`, cancelled the quiet neutral. The
 * operator console adds no colour to the design system — it names the ones already there.
 */
export type PillTone = "active" | "trial" | "pastdue" | "suspended" | "cancelled" | "comp" | "accent";

const TONE: Record<PillTone, string> = {
  active: "bg-present-soft text-present",
  trial: "bg-info-soft text-info",
  pastdue: "bg-late-soft text-late",
  suspended: "bg-danger-soft text-danger",
  cancelled: "bg-ground text-muted",
  comp: "bg-cyan-soft text-cyan",
  accent: "bg-accent-soft text-accent-ink",
};

const BASE = "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold";

/** The base labelled pill. A filled dot backs the colour so meaning never rests on hue alone. */
export function StatusPill({
  tone,
  children,
  className,
}: {
  tone: PillTone;
  children: ReactNode;
  className?: string | undefined;
}) {
  return (
    <span className={cn(BASE, TONE[tone], className)}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {children}
    </span>
  );
}

const ACCOUNT_TONE: Record<AccountStatus, PillTone> = {
  active: "active",
  trial: "trial",
  past_due: "pastdue",
  suspended: "suspended",
  cancelled: "cancelled",
};

const ACCOUNT_LABEL: Record<AccountStatus, string> = {
  active: "Active",
  trial: "Trial",
  past_due: "Past due",
  suspended: "Suspended",
  cancelled: "Cancelled",
};

export function AccountStatusPill({ status }: { status: AccountStatus }) {
  return <StatusPill tone={ACCOUNT_TONE[status]}>{ACCOUNT_LABEL[status]}</StatusPill>;
}

const VERSION_TONE: Record<PlanVersionState, PillTone> = {
  draft: "cancelled",
  published: "active",
  grandfathered: "comp",
};

const VERSION_LABEL: Record<PlanVersionState, string> = {
  draft: "Draft",
  published: "Published",
  grandfathered: "Grandfathered",
};

export function PlanVersionPill({ state }: { state: PlanVersionState }) {
  return <StatusPill tone={VERSION_TONE[state]}>{VERSION_LABEL[state]}</StatusPill>;
}

const INVOICE_TONE: Record<Invoice["state"], PillTone> = {
  draft: "pastdue",
  finalized: "active",
  void: "cancelled",
};

const INVOICE_LABEL: Record<Invoice["state"], string> = {
  draft: "Draft",
  finalized: "Finalized",
  void: "Void",
};

export function InvoiceStatePill({ state }: { state: Invoice["state"] }) {
  return <StatusPill tone={INVOICE_TONE[state]}>{INVOICE_LABEL[state]}</StatusPill>;
}

const LEDGER_TONE: Record<LedgerKind, PillTone> = {
  grant: "active",
  consumption: "cancelled",
  reversal: "pastdue",
};

const LEDGER_LABEL: Record<LedgerKind, string> = {
  grant: "Grant",
  consumption: "Consumption",
  reversal: "Reversal",
};

export function LedgerKindPill({ kind }: { kind: LedgerKind }) {
  return <StatusPill tone={LEDGER_TONE[kind]}>{LEDGER_LABEL[kind]}</StatusPill>;
}

/** "Bespoke" — pinned to an audience-of-one plan version (ADR-0004 §2). */
export function BespokeTag() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-1.5 py-0.5 text-xs font-semibold text-accent-ink">
      <TagIcon className="size-3" /> Bespoke
    </span>
  );
}

/** "Comp" — complimentary access, as against a monetary credit. */
export function CompTag() {
  return (
    <span className="rounded-full bg-cyan-soft px-2 py-0.5 text-xs font-semibold text-cyan">Comp</span>
  );
}
