/**
 * Sends the valid rows, one at a time, through the entity's ordinary create call (ADR-0022 §4).
 *
 * Strictly sequential and throttled: the gateway allows 300 requests per user per minute, shared
 * with everything else the user does. A 429 is the only status ever retried (it says "wait",
 * where a 403 or 422 says "no" and does not fix itself); every other 4xx and every 5xx fails that
 * row once, with the server's own sentence. A 409 that says the record already exists is not a
 * failure: the row is skipped, which is what makes re-uploading the same file safe.
 *
 * The clock and the sleep are injectable so the tests spend no real time.
 */

import { ApiError } from "../http";
import { fieldErrors } from "../problem";
import { isAbortError, prepareMessage, rowMessage } from "./errors";
import type { AnyEntity } from "./cells";
import type { ImportContext, ImportProgress, ParsedRow, RowOutcome, RowStatus } from "./types";
import { RowProblem } from "./types";

export const DEFAULT_MIN_INTERVAL_MS = 350;
export const DEFAULT_BACKOFF_MS = 60_000;
export const DEFAULT_MAX_RETRIES = 3;

/** One row that passed validation and is ready to be sent. */
export interface RunnerRow {
  /** The row's number in the sheet. */
  row: number;
  values: ParsedRow;
  /** The first required cell, so a report row is recognisable without the sheet open. */
  label: string;
}

export interface RunImportArgs {
  entity: AnyEntity;
  rows: readonly RunnerRow[];
  ctx: ImportContext;
  onProgress: (progress: ImportProgress) => void;
  isCancelled: () => boolean;
  sleep?: ((ms: number) => Promise<void>) | undefined;
  now?: (() => number) | undefined;
  /** Minimum time between the starts of two requests. */
  minIntervalMs?: number | undefined;
  /** How long a 429 makes the run wait before the same row is tried again. */
  backoffMs?: number | undefined;
  maxRetries?: number | undefined;
}

export interface RunResult {
  /** In the order sent. Rows never reached because of a cancel are not here. */
  outcomes: RowOutcome[];
  created: number;
  skipped: number;
  failed: number;
  /** Rows the entity's `toPayload` refused when their turn came. */
  invalid: number;
  cancelled: boolean;
}

/**
 * Whether a 409 says "this record already exists". Only that is a skip: it is what makes
 * re-uploading a file safe. A conflict of any other kind (a warehouse with no default bin, an
 * unknown pipeline stage) means this row was refused, and calling it "already exists" would hide
 * a row that was never imported. The services say it either with the `duplicate` field code or in
 * their own sentence ("already exists", "already recorded", "conflicts with something that
 * already exists").
 */
function isDuplicate(error: ApiError): boolean {
  if (fieldErrors(error.problem).some((entry) => entry.code === "duplicate")) return true;
  const sentences = [error.problem?.detail, error.problem?.title, error.message];
  return sentences.some((sentence) => sentence !== undefined && /\balready\b/i.test(sentence));
}

const realSleep = (ms: number): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

export async function runImport(args: RunImportArgs): Promise<RunResult> {
  const { entity, rows, ctx, onProgress, isCancelled } = args;
  const sleep = args.sleep ?? realSleep;
  const now = args.now ?? (() => performance.now());
  const minIntervalMs = args.minIntervalMs ?? DEFAULT_MIN_INTERVAL_MS;
  const backoffMs = args.backoffMs ?? DEFAULT_BACKOFF_MS;
  const maxRetries = args.maxRetries ?? DEFAULT_MAX_RETRIES;

  const outcomes: RowOutcome[] = [];
  const tally: Record<RowStatus, number> = { created: 0, skipped: 0, failed: 0, invalid: 0 };
  let lastStart: number | null = null;

  const report = (waiting: ImportProgress["waiting"] = null): void => {
    onProgress({
      done: outcomes.length,
      total: rows.length,
      created: tally.created,
      skipped: tally.skipped,
      // A row refused while being prepared did not go through either; the live count folds it in
      // here, and the summary keeps it apart as `invalid`.
      failed: tally.failed + tally.invalid,
      waiting,
    });
  };

  const conclude = (
    item: RunnerRow,
    status: RowStatus,
    message: string | null,
    id?: string | undefined,
  ): void => {
    tally[status] += 1;
    outcomes.push({ row: item.row, status, message, id, label: item.label });
    report();
  };

  /** Counts the rate-limit window down a second at a time; false when cancelled meanwhile. */
  const waitOutRateLimit = async (): Promise<boolean> => {
    let remaining = backoffMs;
    while (remaining > 0 && !isCancelled()) {
      report({ reason: "rate-limit", seconds: Math.ceil(remaining / 1000) });
      const step = Math.min(1000, remaining);
      await sleep(step);
      remaining -= step;
    }
    report();
    return !isCancelled();
  };

  /** Sends one row, retrying only a 429. False when the run was cancelled before it concluded. */
  const send = async (item: RunnerRow, payload: unknown): Promise<boolean> => {
    for (let attempt = 0; ; attempt += 1) {
      if (lastStart !== null) {
        const wait = minIntervalMs - (now() - lastStart);
        if (wait > 0) await sleep(wait);
      }
      if (isCancelled()) return false;
      lastStart = now();
      try {
        const created = await entity.create(payload, ctx);
        conclude(item, "created", null, typeof created === "object" && created !== null ? created.id : undefined);
        return true;
      } catch (error) {
        if (isCancelled() && isAbortError(error)) return false;
        if (error instanceof ApiError && error.status === 409 && isDuplicate(error)) {
          conclude(item, "skipped", rowMessage(error));
          return true;
        }
        if (error instanceof ApiError && error.status === 429 && attempt < maxRetries) {
          if (!(await waitOutRateLimit())) return false;
          continue;
        }
        conclude(item, error instanceof RowProblem ? "invalid" : "failed", rowMessage(error));
        return true;
      }
    }
  };

  let cancelled = false;
  report();
  for (const item of rows) {
    if (isCancelled()) {
      cancelled = true;
      break;
    }
    let payload: unknown;
    try {
      payload = entity.toPayload(item.values, ctx);
    } catch (error) {
      conclude(item, "invalid", prepareMessage(error));
      continue;
    }
    if (!(await send(item, payload))) {
      cancelled = true;
      break;
    }
  }

  return { outcomes, ...tally, cancelled };
}
