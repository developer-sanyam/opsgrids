import type { TransferLineOut, TransferReceiveIn, TransferReceiveLineIn } from "@/api/movementTypes";
import { addQty, compareQty, isZeroQty, parseQty } from "./movementQty";

export interface ReceiveLineValues {
  lineId: string;
  received: string;
  shortage: string;
  toLocationId: string;
}

export interface ReceiveLineErrors {
  received?: string;
  shortage?: string;
  location?: string;
  line?: string;
}

export type ReceiveResult =
  | { ok: true; payload: TransferReceiveIn }
  | { ok: false; errors: Record<string, ReceiveLineErrors>; formError?: string };

/** The lines a receipt can still act on: anything with stock left in transit. */
export function receivableLines(lines: readonly TransferLineOut[]): TransferLineOut[] {
  return lines.filter((line) => !isZeroQty(line.in_transit));
}

/** Everything still in transit arrives, nothing is short, into the destination's default bin. */
export function defaultReceiveValues(
  lines: readonly TransferLineOut[],
  defaultBinId: string,
): ReceiveLineValues[] {
  return receivableLines(lines).map((line) => ({
    lineId: line.id,
    received: line.in_transit,
    shortage: "0",
    toLocationId: defaultBinId,
  }));
}

/**
 * Check a receipt against what is still in transit on each line and build the payload.
 *
 * Received plus short can never exceed what is still on its way (the server's 409, said before the
 * round trip). A line with nothing received and nothing short is left out of the request — that is
 * what makes a partial receipt: the line stays in transit for next time. Whatever is declared
 * short is written off, so it is the one figure here that deserves care; both are decimal strings
 * compared in exact milli-units.
 */
export function validateReceive(
  lines: readonly TransferLineOut[],
  values: readonly ReceiveLineValues[],
  discreteUoms: ReadonlySet<string>,
  note: string,
): ReceiveResult {
  const errors: Record<string, ReceiveLineErrors> = {};
  const sent: TransferReceiveLineIn[] = [];

  for (const value of values) {
    const line = lines.find((l) => l.id === value.lineId);
    if (!line) continue;
    const rules = { allowZero: true, whole: discreteUoms.has(line.uom), uom: line.uom };
    const problems: ReceiveLineErrors = {};
    const received = parseQty(value.received === "" ? "0" : value.received, rules);
    const shortage = parseQty(value.shortage === "" ? "0" : value.shortage, rules);
    if (!received.ok) problems.received = received.message;
    if (!shortage.ok) problems.shortage = shortage.message;

    if (received.ok && shortage.ok) {
      const accounted = addQty(received.value, shortage.value);
      if (compareQty(accounted, line.in_transit) > 0) {
        problems.line = `Only ${line.in_transit} ${line.uom} is still in transit — received plus short can't be more.`;
      } else if (!isZeroQty(accounted)) {
        if (!isZeroQty(received.value) && value.toLocationId === "") {
          problems.location = "Choose the bin the stock goes into.";
        }
        if (Object.keys(problems).length === 0) {
          sent.push({
            line_id: line.id,
            received_quantity: received.value,
            shortage_quantity: shortage.value,
            ...(value.toLocationId !== "" ? { to_location_id: value.toLocationId } : {}),
          });
        }
      }
    }
    if (Object.keys(problems).length > 0) errors[line.id] = problems;
  }

  if (Object.keys(errors).length > 0) return { ok: false, errors };
  if (sent.length === 0) {
    return {
      ok: false,
      errors: {},
      formError: "Enter what arrived, or what is short, on at least one line.",
    };
  }
  return { ok: true, payload: { lines: sent, ...(note.trim() !== "" ? { note: note.trim() } : {}) } };
}
