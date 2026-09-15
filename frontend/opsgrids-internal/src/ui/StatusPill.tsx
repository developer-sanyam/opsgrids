import type { ReactNode } from "react";

import type {
  AccountStatus,
  Invoice,
  LedgerKind,
  PlanVersionState,
} from "@/api/types";
import { cn } from "@/lib/cn";

export type PillTone =
  | "active"
  | "trial"
  | "pastdue"
  | "suspended"
  | "cancelled"
  | "comp"
  | "accent";

const TONE: Record<PillTone, string> = {
  active: "bg-active-soft text-active",
  trial: "bg-trial-soft text-trial",
  pastdue: "bg-pastdue-soft text-pastdue",
  suspended: "bg-suspended-soft text-suspended",
  cancelled: "bg-cancelled-soft text-cancelled",
  comp: "bg-comp-soft text-comp",
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
  className?: string;
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
