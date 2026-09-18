import type {
  AssetStatus,
  IncidentStatus,
  Priority,
  RequestStatus,
  SlaState,
} from "@/api/types";
import {
  assetStatusLabel,
  incidentStatusLabel,
  requestStatusLabel,
  slaLabel,
} from "@/lib/format";
import { CheckIcon, cn } from "@opsgrids/ui";

const PILL_BASE =
  "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold whitespace-nowrap";

/* ------------------------------- priority ------------------------------- */

const PRIORITY_TONE: Record<Priority, string> = {
  P1: "bg-danger-soft text-danger",
  P2: "bg-late-soft text-late",
  P3: "bg-info-soft text-info",
  P4: "bg-ground text-muted",
};

const PRIORITY_TEXT: Record<Priority, string> = {
  P1: "Critical",
  P2: "High",
  P3: "Moderate",
  P4: "Low",
};

/**
 * Priority band. The code (P1–P4) and its word both carry the level, so the pill never relies on
 * colour alone. P1 fills a square dot to stand out even in monochrome.
 */
export function PriorityPill({ priority }: { priority: Priority }) {
  return (
    <span className={cn(PILL_BASE, PRIORITY_TONE[priority])} title={PRIORITY_TEXT[priority]}>
      <span className="size-1.5 rounded-[2px] bg-current" aria-hidden />
      <span className="tnum">{priority}</span>
      <span className="font-medium opacity-80">· {PRIORITY_TEXT[priority]}</span>
    </span>
  );
}

/* ---------------------------- incident status ---------------------------- */

const INCIDENT_TONE: Record<IncidentStatus, string> = {
  new: "bg-info-soft text-info",
  in_progress: "bg-cyan-soft text-cyan",
  on_hold: "bg-late-soft text-late",
  resolved: "bg-present-soft text-present",
  closed: "bg-ground text-muted",
};

export function IncidentStatusPill({ status }: { status: IncidentStatus }) {
  return (
    <span className={cn(PILL_BASE, INCIDENT_TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {incidentStatusLabel(status)}
    </span>
  );
}

/* -------------------------------- SLA state ------------------------------- */

const SLA_TONE: Record<SlaState, string> = {
  met: "bg-present-soft text-present",
  on_track: "bg-present-soft text-present",
  at_risk: "bg-late-soft text-late",
  breached: "bg-danger-soft text-danger",
};

/**
 * SLA clock state. Always labelled in words ("On track", "At risk", "Breached", "Met") so the
 * meaning never rests on colour. `met` carries a check glyph to distinguish a satisfied, stopped
 * clock from a still-running `on_track` one that shares its green.
 */
export function SlaPill({ state }: { state: SlaState }) {
  return (
    <span className={cn(PILL_BASE, SLA_TONE[state])}>
      {state === "met" ? (
        <CheckIcon className="size-3" strokeWidth={2.6} aria-hidden />
      ) : (
        <span className="size-1.5 rounded-full bg-current" aria-hidden />
      )}
      {slaLabel(state)}
    </span>
  );
}

/* ---------------------------- request status ----------------------------- */

const REQUEST_TONE: Record<RequestStatus, string> = {
  approval: "bg-late-soft text-late",
  in_progress: "bg-info-soft text-info",
  fulfilled: "bg-present-soft text-present",
};

export function RequestStatusPill({ status }: { status: RequestStatus }) {
  return (
    <span className={cn(PILL_BASE, REQUEST_TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {requestStatusLabel(status)}
    </span>
  );
}

/* ----------------------------- asset status ------------------------------ */

const ASSET_TONE: Record<AssetStatus, string> = {
  in_use: "bg-present-soft text-present",
  in_stock: "bg-info-soft text-info",
  in_repair: "bg-late-soft text-late",
  retired: "bg-ground text-muted",
};

export function AssetStatusPill({ status }: { status: AssetStatus }) {
  return (
    <span className={cn(PILL_BASE, ASSET_TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {assetStatusLabel(status)}
    </span>
  );
}
