/**
 * The new-run form's rules, kept out of the dialog so they can be tested on their own: what makes
 * a set of demand sources and manual lines sendable, and the exact request it becomes. Quantities
 * stay the strings the person typed — never parsed to a number.
 */

import type { MrpDemandSource, MrpManualDemandIn, MrpRunIn } from "@/api/types";
import { cleanDecimal, qtyFieldError } from "@/lib/quantity";

export interface ManualLineState {
  /** A stable React key; never sent. */
  key: string;
  itemId: string | null;
  quantity: string;
  dueDate: string;
  note: string;
}

export interface MrpFormState {
  sales: boolean;
  production: boolean;
  manual: boolean;
  lines: ManualLineState[];
  planDate: string;
  notes: string;
  /** Force the worker even when the run is small. Off = let the server decide by size. */
  background: boolean;
}

export const EMPTY_FORM: MrpFormState = {
  sales: true,
  production: true,
  manual: false,
  lines: [],
  planDate: "",
  notes: "",
  background: false,
};

export function newLine(key: string): ManualLineState {
  return { key, itemId: null, quantity: "", dueDate: "", note: "" };
}

export interface MrpFormResult {
  errors: Record<string, string>;
  input: MrpRunIn | null;
}

export const SOURCES_ERROR = "Choose at least one place to plan from.";

export function lineErrorKey(line: ManualLineState, field: "item" | "quantity"): string {
  return `line.${line.key}.${field}`;
}

/** Validates the form and, when it is sendable, builds the request body. */
export function buildMrpRun(state: MrpFormState): MrpFormResult {
  const errors: Record<string, string> = {};
  const sources: MrpDemandSource[] = [
    ...(state.sales ? (["SALES_ORDER"] as const) : []),
    ...(state.production ? (["PRODUCTION_ORDER"] as const) : []),
    ...(state.manual ? (["MANUAL"] as const) : []),
  ];
  if (sources.length === 0) errors.sources = SOURCES_ERROR;

  const manual: MrpManualDemandIn[] = [];
  if (state.manual) {
    if (state.lines.length === 0) {
      errors.sources = "Add at least one manual demand line, or turn manual demand off.";
    }
    for (const line of state.lines) {
      if (line.itemId === null) errors[lineErrorKey(line, "item")] = "Choose an item.";
      const quantityError = qtyFieldError(line.quantity);
      if (quantityError) errors[lineErrorKey(line, "quantity")] = quantityError;
      if (line.itemId !== null && !quantityError) {
        manual.push({
          item_id: line.itemId,
          quantity: cleanDecimal(line.quantity),
          ...(line.dueDate !== "" ? { due_date: line.dueDate } : {}),
          ...(line.note.trim() !== "" ? { note: line.note.trim() } : {}),
        });
      }
    }
  }

  if (Object.keys(errors).length > 0) return { errors, input: null };
  return {
    errors,
    input: {
      sources,
      ...(manual.length > 0 ? { manual_demands: manual } : {}),
      ...(state.planDate !== "" ? { plan_date: state.planDate } : {}),
      ...(state.notes.trim() !== "" ? { notes: state.notes.trim() } : {}),
      mode: state.background ? "worker" : "auto",
    },
  };
}

/** The sentence under the form: where this run would be computed, from the server's own count. */
export function whereItRuns(
  state: Pick<MrpFormState, "background">,
  preview: { mode_if_auto: "sync" | "worker" } | null,
): string | null {
  if (state.background) return "It will be queued and computed in the background.";
  if (preview === null) return null;
  return preview.mode_if_auto === "worker"
    ? "There is a lot of open demand, so it will be queued and computed in the background."
    : "It will be computed now.";
}
