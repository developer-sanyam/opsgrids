/**
 * Pure quality rules the screens share (inventory plan Phase 4). Nothing here does arithmetic on a
 * quantity: quantities, bounds and readings stay decimal **strings** end to end, and the one thing
 * checked client-side is that a typed value is the shape the server accepts — so a typo is caught
 * before a round trip, never "corrected" into a float.
 */

import { useMemo } from "react";

import { qtyToMilli } from "@/lib/quantity";
import type { InspectionOut, InspectionStatus, QcParamIn, QcParamOut } from "@/api/types";

/** At most three decimals, optional sign — exactly `app.schemas.Qty`. */
const DECIMAL = /^-?\d+(\.\d{1,3})?$/;

export function isDecimal(value: string): boolean {
  return DECIMAL.test(value.trim());
}

/** A positive decimal string: the shape of a quantity the server will take as "more than zero". */
export function isPositiveDecimal(value: string): boolean {
  const v = value.trim();
  return DECIMAL.test(v) && !v.startsWith("-") && /[1-9]/.test(v);
}

/**
 * A key that is stable for one intended request and changes when the request does. Retrying a
 * submit after a lost answer re-sends the same key (the server replays); editing the form first
 * makes it a different request, so it gets a different key — reusing one for a different payload
 * is a 409 by design.
 */
export function useIdempotencyKey(fingerprint: string): string {
  // eslint-disable-next-line react-hooks/exhaustive-deps -- the fingerprint IS the dependency
  return useMemo(() => crypto.randomUUID(), [fingerprint]);
}

export interface InspectionPerms {
  inspect: boolean;
  approve: boolean;
  reject: boolean;
}

export type InspectionAction =
  | "record"
  | "hold"
  | "rework"
  | "reinspect"
  | "release"
  | "reject"
  | "dispose";

const OPEN: ReadonlySet<InspectionStatus> = new Set(["PENDING", "HOLD"]);

/**
 * Which actions an inspection offers this person — a mirror of the server's state machine and
 * permission floors, used only to *hide* what would be refused. It is not the protection: every
 * route re-checks both on the server.
 */
export function availableActions(
  inspection: Pick<InspectionOut, "status" | "rejected" | "disposition">,
  perms: InspectionPerms,
): InspectionAction[] {
  const { status } = inspection;
  const out: InspectionAction[] = [];
  if (perms.inspect && OPEN.has(status)) out.push("record");
  if (perms.inspect && status === "PENDING") out.push("hold");
  if (perms.inspect && OPEN.has(status)) out.push("rework");
  if (perms.inspect && status === "REWORK") out.push("reinspect");
  if (perms.approve && OPEN.has(status)) out.push("release");
  if (perms.reject && (OPEN.has(status) || status === "REWORK")) out.push("reject");
  const hasRejectedStock = inspection.rejected !== null && /[1-9]/.test(inspection.rejected);
  if (
    perms.reject &&
    (status === "FAILED" || status === "PARTIAL_PASS") &&
    hasRejectedStock &&
    inspection.disposition === "NONE"
  ) {
    out.push("dispose");
  }
  return out;
}

/** `"170"`/`"190"` → `170 – 190`; one open side → `≤ 3.5` / `≥ 170`; neither → `—`. */
export function formatBounds(p: Pick<QcParamOut, "min" | "max" | "kind">): string {
  if (p.kind === "pass_fail") return "Pass / fail";
  if (p.min !== null && p.max !== null) return `${p.min} – ${p.max}`;
  if (p.max !== null) return `≤ ${p.max}`;
  if (p.min !== null) return `≥ ${p.min}`;
  return "—";
}

/** A parameter row as typed in the template form → the request shape, with blanks as `null`. */
export interface ParamDraft {
  key: string;
  name: string;
  kind: "numeric" | "pass_fail";
  unit: string;
  min: string;
  max: string;
  is_critical: boolean;
}

export function toParamIn(draft: ParamDraft): QcParamIn {
  const numeric = draft.kind === "numeric";
  return {
    name: draft.name.trim(),
    kind: draft.kind,
    unit: draft.unit.trim(),
    min: numeric && draft.min.trim() !== "" ? draft.min.trim() : null,
    max: numeric && draft.max.trim() !== "" ? draft.max.trim() : null,
    is_critical: draft.is_critical,
  };
}

/** The first problem with a template's parameter drafts, in the user's own words, or `undefined`. */
export function validateParams(drafts: ParamDraft[]): string | undefined {
  const seen = new Set<string>();
  for (const d of drafts) {
    const name = d.name.trim();
    if (name === "") return "Every parameter needs a name.";
    if (seen.has(name.toLowerCase())) return `"${name}" appears twice.`;
    seen.add(name.toLowerCase());
    if (d.kind === "numeric") {
      for (const [label, value] of [
        ["minimum", d.min],
        ["maximum", d.max],
      ] as const) {
        if (value.trim() !== "" && !isDecimal(value)) {
          return `${name}: the ${label} must be a number with at most three decimals.`;
        }
      }
      // Exact, via integer milli-units — never a float comparison.
      if (
        d.min.trim() !== "" &&
        d.max.trim() !== "" &&
        qtyToMilli(d.min.trim()) > qtyToMilli(d.max.trim())
      ) {
        return `${name}: the minimum is above the maximum.`;
      }
    }
  }
  return undefined;
}
