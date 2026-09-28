import type { PickTaskLineOut } from "@/api/types";
import { qtyToMilli } from "@/lib/quantity";

/** What is still owed on this line: the whole request minus what has already been picked or
 *  written off short. Computed client-side in integer milli-units so a 3-decimal quantity never
 *  drifts the way a float subtraction would (ADR-0014 §2, `lib/quantity.ts`). */
export function remainingMilli(line: PickTaskLineOut): number {
  return qtyToMilli(line.quantity) - qtyToMilli(line.picked) - qtyToMilli(line.short);
}

/** The picker's own walk order — `sequence` is F1's `pick_sequence`, carried onto the task line.
 *  A line with no sequence (not derived from a sequenced location) sorts last rather than first,
 *  so it never jumps ahead of a real walk order. */
export function sortedLines(lines: PickTaskLineOut[]): PickTaskLineOut[] {
  return [...lines].sort((a, b) => {
    if (a.sequence === null && b.sequence === null) return 0;
    if (a.sequence === null) return 1;
    if (b.sequence === null) return -1;
    return a.sequence - b.sequence;
  });
}

export const LINE_STATUS_LABEL: Record<PickTaskLineOut["status"], string> = {
  pending: "Not picked",
  partial: "Partially picked",
  picked: "Picked",
  short: "Short",
};
