import type {
  AssetStatus,
  IncidentStatus,
  Priority,
  RequestStatus,
  SlaState,
} from "@/api/types";
import { StatusBadge } from "@opsgrids/ui";
import type { StatusTone } from "@opsgrids/ui";

/* ------------------------------- priority ------------------------------- */

/** `P1`–`P4` aren't words the shared registry knows — they're ITSM's own severity codes — so
 *  every one takes an explicit tone. Missing from the registry: `P1`, `P2`, `P3`, `P4`. */
const PRIORITY_TONE: Record<Priority, StatusTone> = {
  P1: "danger",
  P2: "warning",
  P3: "info",
  P4: "neutral",
};

const PRIORITY_TEXT: Record<Priority, string> = {
  P1: "Critical",
  P2: "High",
  P3: "Moderate",
  P4: "Low",
};

/** Priority band. The code (P1–P4) and its word both carry the level, on the tone's tint. */
export function PriorityPill({ priority }: { priority: Priority }) {
  return <StatusBadge tone={PRIORITY_TONE[priority]} label={`${priority} · ${PRIORITY_TEXT[priority]}`} />;
}

/* ---------------------------- incident status ---------------------------- */

/** Every incident status is a registered word — the registry's tone applies as-is. */
export function IncidentStatusPill({ status }: { status: IncidentStatus }) {
  return <StatusBadge status={status} />;
}

/* -------------------------------- SLA state ------------------------------- */

/** Every SLA state is a registered word too. */
export function SlaPill({ state }: { state: SlaState }) {
  return <StatusBadge status={state} />;
}

/* ---------------------------- request status ----------------------------- */

/** `approval` isn't registered (the registry has `approved`, a different word) — it takes an
 *  explicit tone and ITSM's own wording. Missing from the registry: `approval`. */
export function RequestStatusPill({ status }: { status: RequestStatus }) {
  if (status === "approval") return <StatusBadge tone="warning" label="Awaiting approval" />;
  return <StatusBadge status={status} />;
}

/* ----------------------------- asset status ------------------------------ */

/** `in_use`, `in_stock` and `in_repair` aren't registered words — ITSM's own asset lifecycle —
 *  so they take an explicit tone. Missing from the registry: `in_use`, `in_stock`, `in_repair`.
 *  `retired` is registered and gets the shared tone. */
export function AssetStatusPill({ status }: { status: AssetStatus }) {
  if (status === "in_use") return <StatusBadge tone="success" label="In use" />;
  if (status === "in_stock") return <StatusBadge tone="info" label="In stock" />;
  if (status === "in_repair") return <StatusBadge tone="warning" label="In repair" />;
  return <StatusBadge status={status} />;
}
