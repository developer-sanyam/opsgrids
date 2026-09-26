import { ApiError } from "../http";
import type { ExportPage, Lookup } from "./types";

function normalise(label: string): string {
  return label.trim().replace(/\s+/g, " ").toLowerCase();
}

/**
 * A `Lookup` over `(label, value)` pairs. Matching ignores case and runs of spaces. A label that
 * more than one entry carries *for different values* is ambiguous: `resolve` returns undefined
 * rather than pick one, so the row is refused instead of landing on the wrong record. The same
 * label repeated for the same value (a unit listed by code and by name) is not ambiguous.
 */
export function makeLookup(entries: readonly { label: string; value: string }[]): Lookup {
  const byLabel = new Map<string, string | null>();
  const byValue = new Map<string, string>();
  for (const { label, value } of entries) {
    const key = normalise(label);
    byLabel.set(key, byLabel.has(key) && byLabel.get(key) !== value ? null : value);
    if (!byValue.has(value)) byValue.set(value, label);
  }
  return {
    labels: entries.map((e) => e.label),
    resolve: (label) => byLabel.get(normalise(label)) ?? undefined,
    isAmbiguous: (label) => byLabel.get(normalise(label)) === null,
    labelOf: (value) => byValue.get(value),
  };
}

// ---------------------------------------------------------------------------------- pacing

/** Paging requests (lookups and exports together) allowed per rolling minute. The gateway allows
 *  a user 300 a minute for everything they do, so this leaves the rest of the app its share. */
const PAGE_BUDGET_PER_MINUTE = 150;
const WINDOW_MS = 60_000;
/** The gateway's window is 60 s, so that is how long a 429 means "wait". */
const RATE_LIMIT_WAIT_MS = 60_000;
const RATE_LIMIT_RETRIES = 2;

const recentRequests: number[] = [];

function sleep(ms: number, signal: AbortSignal | undefined): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted === true) {
      reject(signal.reason);
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = (): void => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * Waits, only when needed, until one more paging request fits the budget. A small lookup never
 * waits; a 12 000-row export that also loads three reference lists does, instead of being
 * refused with a 429 half way through. Shared by every caller in the page.
 */
export async function acquirePageSlot(signal?: AbortSignal): Promise<void> {
  for (;;) {
    const now = Date.now();
    while (recentRequests.length > 0 && now - (recentRequests[0] ?? now) >= WINDOW_MS) {
      recentRequests.shift();
    }
    if (recentRequests.length < PAGE_BUDGET_PER_MINUTE) {
      recentRequests.push(now);
      return;
    }
    const oldest = recentRequests[0] ?? now;
    await sleep(Math.max(1, WINDOW_MS - (now - oldest)), signal);
  }
}

/** Runs one paging request through the budget, waiting out a 429 (the only status that is ever
 *  retried: it says wait, not no) up to `RATE_LIMIT_RETRIES` times. */
export async function pagedRequest<R>(
  request: () => Promise<R>,
  signal?: AbortSignal,
): Promise<R> {
  for (let attempt = 0; ; attempt += 1) {
    signal?.throwIfAborted();
    await acquirePageSlot(signal);
    try {
      return await request();
    } catch (error) {
      if (error instanceof ApiError && error.status === 429 && attempt < RATE_LIMIT_RETRIES) {
        await sleep(RATE_LIMIT_WAIT_MS, signal);
        continue;
      }
      throw error;
    }
  }
}

/** Forgets the budget: for tests only. */
export function resetPageBudgetForTests(): void {
  recentRequests.length = 0;
}

// ------------------------------------------------------------------------ reading a whole list

export interface CollectOptions<T> {
  size?: number;
  maxPages?: number;
  signal?: AbortSignal;
  /** Identifies a record. With it, a record served twice (offset paging over a non-unique order
   *  can repeat one and skip another) is kept once, and a list that still comes back short of the
   *  server's own total is refused rather than handed over partial. Pass it whenever the list
   *  has an id. */
  key?: (item: T) => string;
}

async function walk<T>(
  fetchPage: (page: number, size: number) => Promise<ExportPage<T>>,
  size: number,
  maxPages: number,
  signal: AbortSignal | undefined,
): Promise<{ items: T[]; total: number; capped: boolean }> {
  const items: T[] = [];
  let total = 0;
  let capped = false;
  for (let page = 1; page <= maxPages; page += 1) {
    const result = await pagedRequest(() => fetchPage(page, size), signal);
    items.push(...result.items);
    total = result.total;
    if (page >= result.pages || result.items.length === 0) break;
    if (page === maxPages) capped = true;
  }
  return { items, total, capped };
}

/**
 * Every row of a paged list, for reference data. The screens' own "directory" loads stop at one
 * page of 200, which silently truncates a larger organisation, so a lookup built from them would
 * call a real company unknown.
 *
 * Never returns a partial list without saying so. It rejects when the list is longer than
 * `maxPages` pages (default 50, so 10 000 rows at the default size), and, with `key`, when a
 * second walk still comes back short of the server's total: the reference cells the caller
 * builds from it would otherwise be blank or refused for records that exist. Rejects with the
 * request's own error otherwise; honours `signal`; paces itself against the gateway's limit.
 */
export async function collectPages<T>(
  fetchPage: (page: number, size: number) => Promise<ExportPage<T>>,
  options: CollectOptions<T> = {},
): Promise<T[]> {
  const size = Math.min(options.size ?? 200, 200);
  const maxPages = options.maxPages ?? 50;
  const { key, signal } = options;

  const unique = (items: T[]): T[] => {
    if (key === undefined) return items;
    const seen = new Set<string>();
    return items.filter((item) => {
      const id = key(item);
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  };

  let last: T[] = [];
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const { items, total, capped } = await walk(fetchPage, size, maxPages, signal);
    if (capped && total > maxPages * size) {
      throw new Error(
        `This list has more than ${(maxPages * size).toLocaleString("en-US")} records, which is more than an import can look up.`,
      );
    }
    last = unique(items);
    if (last.length >= total) return last;
  }
  throw new Error("The list changed while it was being read. Try again in a moment.");
}
