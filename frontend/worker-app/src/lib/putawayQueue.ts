/**
 * The storage put-away queue (spec §6): what a worker confirmed while the signal was bad, kept on
 * the phone until the server has it.
 *
 * Its own queue, not `scanQueue`'s. A scan is an append-only log row that costs nothing to repeat;
 * a put-away is a stock move, and what makes resending it safe is a per-entry idempotency key the
 * server honours. So an entry freezes everything about the request at the moment of confirming —
 * the body, the random key, `captured_at`, `occurred_on` (a UTC date, so a replay a day later
 * still reports the day the stock moved) — and every resend is that same request, byte for byte.
 *
 * Entries belong to the person who made them (`ownerSub`, plus the organization they were signed
 * in to). Another account on the same phone neither replays them nor sees them. Signing out purges
 * the lot (`useSignOut`). A GPS fix is personal data: it is kept only as long as the entry might
 * still be sent — dropped with the entry on success, and stripped from one that is parked, since a
 * parked entry is never sent again.
 *
 * `localStorage` is the home, but it is allowed to be unavailable (private browsing, a locked-down
 * WebView, a full quota). Then the queue lives in memory for the session: the put-away still
 * syncs if the connection returns before the app is closed, and `isPutawayQueuePersistent` lets
 * the screen say plainly that closing it would lose what is waiting.
 *
 * A module-level store with a subscription rather than component state, so the app-level drainer,
 * the put-away screen and the sign-out purge — none of which share a parent that could own it —
 * all see one queue.
 */

import type { PutawayIn } from "@/api/types";
import { newIdempotencyKey } from "@/lib/idempotencyKey";

const STORAGE_KEY = "opsgrids.worker.putawayQueue";

export type PutawayEntryStatus = "pending" | "needs_attention";

/** What the queue panel names an entry by. Display only — never sent, never trusted. */
export interface PutawaySummary {
  sku: string;
  itemName: string;
  uom: string;
  fromLocationCode: string;
  /** The label as scanned or typed. */
  toBarcode: string;
}

/** Why the server refused an entry for good. */
export interface PutawayAttention {
  httpStatus: number;
  message: string;
}

export interface PutawayQueueEntry {
  ownerSub: string;
  organizationId: string | null;
  /** Random at enqueue; the same on every resend. */
  idempotencyKey: string;
  /** Frozen at enqueue. Carries `captured_at`, `occurred_on` and, until it syncs, the GPS fix. */
  body: PutawayIn;
  summary: PutawaySummary;
  queuedAt: string;
  status: PutawayEntryStatus;
  attention: PutawayAttention | null;
}

export interface PutawayOwner {
  ownerSub: string;
  organizationId: string | null;
}

export function isOwnedBy(entry: PutawayQueueEntry, owner: PutawayOwner): boolean {
  return entry.ownerSub === owner.ownerSub && entry.organizationId === owner.organizationId;
}

const EMPTY: readonly PutawayQueueEntry[] = [];

let snapshot: readonly PutawayQueueEntry[] = EMPTY;
/** The raw string `snapshot` was parsed from, so an unchanged store hands back the same array. */
let lastRaw: string | null = null;
/** Storage refused a read or a write: the in-memory copy is the truth until the page reloads. */
let memoryOnly = false;
const listeners = new Set<() => void>();

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isEntry(value: unknown): value is PutawayQueueEntry {
  if (!isRecord(value)) return false;
  const { body, summary, attention } = value;
  return (
    typeof value.ownerSub === "string" &&
    (value.organizationId === null || typeof value.organizationId === "string") &&
    typeof value.idempotencyKey === "string" &&
    typeof value.queuedAt === "string" &&
    (value.status === "pending" || value.status === "needs_attention") &&
    isRecord(body) &&
    typeof body.inventory_item_id === "string" &&
    typeof body.from_location_id === "string" &&
    typeof body.to_barcode === "string" &&
    typeof body.quantity === "string" &&
    isRecord(summary) &&
    typeof summary.sku === "string" &&
    (attention === null || (isRecord(attention) && typeof attention.message === "string"))
  );
}

function parse(raw: string | null): readonly PutawayQueueEntry[] {
  if (!raw) return EMPTY;
  try {
    const parsed: unknown = JSON.parse(raw);
    // An entry that does not look like one is dropped rather than sent: a corrupt or foreign
    // value under this key must never become a request.
    return Array.isArray(parsed) ? parsed.filter(isEntry) : EMPTY;
  } catch {
    return EMPTY;
  }
}

/** The queue as it stands now — re-read from storage, so another tab's change is seen. */
export function getPutawayQueue(): readonly PutawayQueueEntry[] {
  if (memoryOnly) return snapshot;
  let raw: string | null;
  try {
    raw = localStorage.getItem(STORAGE_KEY);
  } catch {
    memoryOnly = true;
    return snapshot;
  }
  if (raw === lastRaw) return snapshot;
  lastRaw = raw;
  snapshot = parse(raw);
  return snapshot;
}

/** `false` once the browser has refused to store the queue: what is waiting will not survive the
 *  app being closed. */
export function isPutawayQueuePersistent(): boolean {
  return !memoryOnly;
}

function emit(): void {
  for (const listener of listeners) listener();
}

function onStorage(event: StorageEvent): void {
  if (event.key === null || event.key === STORAGE_KEY) emit();
}

export function subscribePutawayQueue(listener: () => void): () => void {
  if (listeners.size === 0) window.addEventListener("storage", onStorage);
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) window.removeEventListener("storage", onStorage);
  };
}

function mutate(change: (current: readonly PutawayQueueEntry[]) => readonly PutawayQueueEntry[]): void {
  const current = getPutawayQueue();
  const next = change(current);
  if (next === current) return;
  const raw = next.length > 0 ? JSON.stringify(next) : null;
  if (!memoryOnly) {
    try {
      if (raw === null) localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, raw);
      lastRaw = raw;
    } catch {
      memoryOnly = true;
    }
  }
  snapshot = next;
  emit();
}

export interface EnqueuePutawayInput extends PutawayOwner {
  body: PutawayIn;
  summary: PutawaySummary;
}

/** Freeze a put-away and queue it. The key is minted here and never changes. */
export function enqueuePutaway(input: EnqueuePutawayInput): PutawayQueueEntry {
  const entry: PutawayQueueEntry = {
    ownerSub: input.ownerSub,
    organizationId: input.organizationId,
    idempotencyKey: newIdempotencyKey(),
    body: Object.freeze({ ...input.body }),
    summary: { ...input.summary },
    queuedAt: new Date().toISOString(),
    status: "pending",
    attention: null,
  };
  mutate((current) => [...current, entry]);
  return entry;
}

/** Drop one entry — synced, refused on the spot, or discarded by the worker. Its body, and any
 *  coordinates in it, go with it. */
export function removePutaway(idempotencyKey: string): void {
  mutate((current) =>
    current.some((entry) => entry.idempotencyKey === idempotencyKey)
      ? current.filter((entry) => entry.idempotencyKey !== idempotencyKey)
      : current,
  );
}

function withoutGeo(body: PutawayIn): PutawayIn {
  const copy: PutawayIn = { ...body };
  delete copy.lat_micro;
  delete copy.lng_micro;
  delete copy.accuracy_m;
  return copy;
}

/** The server said no and will keep saying it: hold the entry for the worker to read and
 *  discard. It is never sent again, so its coordinates are dropped now. */
export function parkPutaway(idempotencyKey: string, attention: PutawayAttention): void {
  mutate((current) =>
    current.map((entry) =>
      entry.idempotencyKey === idempotencyKey
        ? { ...entry, status: "needs_attention" as const, attention, body: withoutGeo(entry.body) }
        : entry,
    ),
  );
}

/** Empty the queue — everyone's entries, not just the signed-in user's. Sign-out calls this. */
export function purgePutawayQueue(): void {
  snapshot = EMPTY;
  lastRaw = null;
  memoryOnly = false;
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing was stored that this could remove; the in-memory queue is already empty.
  }
  emit();
}
