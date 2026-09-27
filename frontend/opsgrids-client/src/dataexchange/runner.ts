/**
 * Sends the valid rows, one at a time, through the entity's ordinary create call (ADR-0022 §4), or,
 * for a row that updates a stored record (update mode, ADR-0022 addendum), through its update plan:
 * one PATCH with only the changed fields, then any extra steps (a deal's stage, a contact's phone).
 *
 * Strictly sequential and throttled: the gateway allows 300 requests per user per minute, shared
 * with everything else the user does, so every REQUEST starts at least `minIntervalMs` after the
 * one before (a row with extra steps sends several). A 429 is the only status ever retried (it says
 * "wait", where a 403 or 422 says "no" and does not fix itself). A 429 request never executed, so
 * only that request is tried again: the steps of an update row that already went are not repeated.
 * Every other 4xx and every 5xx fails that row once, with the server's own sentence.
 *
 * A 409 that says the record already exists is a skip when adding, which is what makes re-uploading
 * the same file safe. When updating it is always a failure: "a company with that name already
 * exists" on a rename is a collision the user must see, and a new record that meets an existing one
 * means the sheet's values were not applied.
 *
 * The clock and the sleep are injectable so the tests spend no real time.
 */

import { ApiError } from "../http";
import { fieldErrors } from "../problem";
import { UNCHANGED_MESSAGE, isAbortError, prepareMessage, rowMessage } from "./errors";
import type { AnyEntity } from "./cells";
import type { ImportContext, ImportMode, ImportProgress, ParsedRow, RowOutcome, RowStatus, UpdatePlan } from "./types";
import { RowProblem } from "./types";

export const DEFAULT_MIN_INTERVAL_MS = 350;
export const DEFAULT_BACKOFF_MS = 60_000;
export const DEFAULT_MAX_RETRIES = 3;

/** Said of a failed update row when an earlier request of the same row had already been accepted. */
export const PARTLY_SAVED_MESSAGE = "The earlier changes to this row were saved.";
export const STOPPED_PARTWAY_MESSAGE = "The import was stopped after some of this row's changes were saved.";

/** One row that passed validation and is ready to be sent. */
export interface RunnerRow {
  /** The row's number in the sheet. */
  row: number;
  values: ParsedRow;
  /** The first required cell, so a report row is recognisable without the sheet open. */
  label: string;
  /** Present on a row that updates a stored record instead of creating one: its plan for this run,
   *  or null when it turns out to change nothing. */
  update?: { plan(ctx: ImportContext): UpdatePlan | null } | undefined;
}

export interface RunImportArgs {
  entity: AnyEntity;
  rows: readonly RunnerRow[];
  ctx: ImportContext;
  onProgress: (progress: ImportProgress) => void;
  isCancelled: () => boolean;
  /** `addAndUpdate` makes every 409 a failure. Default `add`. */
  mode?: ImportMode | undefined;
  sleep?: ((ms: number) => Promise<void>) | undefined;
  now?: (() => number) | undefined;
  /** Minimum time between the starts of two requests. */
  minIntervalMs?: number | undefined;
  /** How long a 429 makes the run wait before the same request is tried again. */
  backoffMs?: number | undefined;
  maxRetries?: number | undefined;
}

export interface RunResult {
  /** In the order sent. Rows never reached because of a cancel are not here. */
  outcomes: RowOutcome[];
  created: number;
  updated: number;
  /** Update rows that turned out to change nothing when their turn came. */
  unchanged: number;
  skipped: number;
  failed: number;
  /** Rows the entity's `toPayload` (or the update plan) refused when their turn came. */
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

/** Thrown out of an update plan when the run was cancelled before one of its requests could start. */
class Stopped extends Error {}

export async function runImport(args: RunImportArgs): Promise<RunResult> {
  const { entity, rows, ctx, onProgress, isCancelled } = args;
  const sleep = args.sleep ?? realSleep;
  const now = args.now ?? (() => performance.now());
  const minIntervalMs = args.minIntervalMs ?? DEFAULT_MIN_INTERVAL_MS;
  const backoffMs = args.backoffMs ?? DEFAULT_BACKOFF_MS;
  const maxRetries = args.maxRetries ?? DEFAULT_MAX_RETRIES;
  const updating = args.mode === "addAndUpdate";

  const outcomes: RowOutcome[] = [];
  const tally: Record<RowStatus, number> = { created: 0, updated: 0, unchanged: 0, skipped: 0, failed: 0, invalid: 0 };
  let lastStart: number | null = null;

  const report = (waiting: ImportProgress["waiting"] = null): void => {
    onProgress({
      done: outcomes.length,
      total: rows.length,
      created: tally.created,
      updated: tally.updated,
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
    extra: { id?: string | undefined; changes?: readonly string[] | undefined } = {},
  ): void => {
    tally[status] += 1;
    outcomes.push({ row: item.row, status, message, id: extra.id, changes: extra.changes, label: item.label });
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

  /**
   * One request, in its turn: waits for the spacing since the last request started, then sends it,
   * waiting out a 429 and sending the same request again (up to `maxRetries` times). Any other
   * error is the caller's. `undefined` when the run was cancelled before the request concluded.
   */
  const attempt = async <T>(request: () => Promise<T>): Promise<{ value: T } | undefined> => {
    for (let tries = 0; ; tries += 1) {
      if (lastStart !== null) {
        const wait = minIntervalMs - (now() - lastStart);
        if (wait > 0) await sleep(wait);
      }
      if (isCancelled()) return undefined;
      lastStart = now();
      try {
        return { value: await request() };
      } catch (error) {
        if (isCancelled() && isAbortError(error)) return undefined;
        if (error instanceof ApiError && error.status === 429 && tries < maxRetries) {
          if (!(await waitOutRateLimit())) return undefined;
          continue;
        }
        throw error;
      }
    }
  };

  /** Creates one record. False when the run was cancelled before it concluded. */
  const create = async (item: RunnerRow, payload: unknown): Promise<boolean> => {
    let sent: { value: { id?: string } | undefined | void } | undefined;
    try {
      sent = await attempt(() => entity.create(payload, ctx));
    } catch (error) {
      if (!updating && error instanceof ApiError && error.status === 409 && isDuplicate(error)) {
        conclude(item, "skipped", rowMessage(error));
      } else {
        conclude(item, error instanceof RowProblem ? "invalid" : "failed", rowMessage(error));
      }
      return true;
    }
    if (sent === undefined) return false;
    const { value } = sent;
    conclude(item, "created", null, { id: typeof value === "object" && value !== null ? value.id : undefined });
    return true;
  };

  /** Updates one record through its plan. False when the run was cancelled before it concluded. */
  const update = async (item: RunnerRow, planOf: (ctx: ImportContext) => UpdatePlan | null): Promise<boolean> => {
    let plan: UpdatePlan | null;
    try {
      plan = planOf(ctx);
    } catch (error) {
      conclude(item, "invalid", prepareMessage(error));
      return true;
    }
    if (plan === null) {
      conclude(item, "unchanged", UNCHANGED_MESSAGE);
      return true;
    }
    const { changed } = plan;
    let saved = 0;
    try {
      await plan.run(async (step) => {
        if ((await attempt(step)) === undefined) throw new Stopped();
        saved += 1;
      });
    } catch (error) {
      if (error instanceof Stopped) {
        // Nothing to say about a row that never started; a row half done must not vanish from the report.
        if (saved > 0) conclude(item, "failed", STOPPED_PARTWAY_MESSAGE, { changes: changed });
        return false;
      }
      const reason = rowMessage(error);
      conclude(item, error instanceof RowProblem ? "invalid" : "failed", saved > 0 ? `${reason} ${PARTLY_SAVED_MESSAGE}` : reason, {
        changes: changed,
      });
      return true;
    }
    conclude(item, "updated", null, { changes: changed });
    return true;
  };

  let cancelled = false;
  report();
  for (const item of rows) {
    if (isCancelled()) {
      cancelled = true;
      break;
    }
    let concluded: boolean;
    if (item.update !== undefined) {
      concluded = await update(item, item.update.plan);
    } else {
      let payload: unknown;
      try {
        payload = entity.toPayload(item.values, ctx);
      } catch (error) {
        conclude(item, "invalid", prepareMessage(error));
        continue;
      }
      concluded = await create(item, payload);
    }
    if (!concluded) {
      cancelled = true;
      break;
    }
  }

  return { outcomes, ...tally, cancelled };
}
