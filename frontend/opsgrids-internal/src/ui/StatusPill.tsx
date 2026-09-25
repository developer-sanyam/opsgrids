import type { ReactNode } from "react";

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
  active: "bg-present-soft text-present",
  trial: "bg-info-soft text-info",
  pastdue: "bg-late-soft text-late",
  suspended: "bg-danger-soft text-danger",
  cancelled: "bg-neutral-soft text-neutral",
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
  className?: string;
}) {
  return (
    <span className={cn(BASE, TONE[tone], className)}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {children}
    </span>
  );
}

const KNOWN_ACCOUNT_TONE: Record<string, PillTone> = {
  active: "active",
  trial: "trial",
  past_due: "pastdue",
  suspended: "suspended",
  cancelled: "cancelled",
};

const KNOWN_ACCOUNT_LABEL: Record<string, string> = {
  active: "Active",
  trial: "Trial",
  past_due: "Past due",
  suspended: "Suspended",
  cancelled: "Cancelled",
};

/** A lifecycle status pill. Several of the console's status fields are a free `str` server-side
 *  (`OrgDetailOut.status`, `SubscriptionOut.state`, `ProductSubscriptionOut.state`), not a closed
 *  enum — an unrecognized value falls back to a plain, neutral pill with the raw text rather than
 *  throwing on a lookup miss. */
export function AccountStatusPill({ status }: { status: string }) {
  const tone = KNOWN_ACCOUNT_TONE[status] ?? "accent";
  const label = KNOWN_ACCOUNT_LABEL[status] ?? status;
  return <StatusPill tone={tone}>{label}</StatusPill>;
}

const KNOWN_LEDGER_TONE: Record<string, PillTone> = {
  grant: "active",
  consume: "cancelled",
  reversal: "pastdue",
  expiry: "suspended",
};

const KNOWN_LEDGER_LABEL: Record<string, string> = {
  grant: "Grant",
  consume: "Consumption",
  reversal: "Reversal",
  expiry: "Expiry",
};

/** `entry_type` is a free `str` at the API boundary too (constrained only by a database check
 *  constraint the client can't see), so this falls back the same way `AccountStatusPill` does. */
export function LedgerKindPill({ kind }: { kind: string }) {
  const tone = KNOWN_LEDGER_TONE[kind] ?? "accent";
  const label = KNOWN_LEDGER_LABEL[kind] ?? kind;
  return <StatusPill tone={tone}>{label}</StatusPill>;
}
