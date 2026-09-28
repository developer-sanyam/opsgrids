/**
 * The scan screen's offline queue (plan F3's exit criterion: "offline-tolerant for the scan
 * screen — queues scans locally and replays on reconnect"). `localStorage`, not IndexedDB — one
 * small array of small objects, well inside `localStorage`'s size limits, and synchronous reads
 * keep the queue's own state simple.
 *
 * Scoped to `scan_events` on purpose: it is the one fulfilment write safe to queue-and-replay
 * blind. It is append-only and has no stock consequence (ADR-0024 §7's "so a scan queued offline
 * and replayed is a replay, not a second move" — that guarantee is a per-scan idempotency key
 * this backend does not accept yet, see this app's `README.md`; queueing scans is still the right
 * default because a duplicate scan-log row costs nothing the way a duplicate stock move would).
 * Pick and pack confirmations are never queued here — those are stock-mutating and go through
 * `useMutation` online-only, matching this file's own header note on the lack of a server
 * idempotency key for those two calls.
 */

import type { ScanIn } from "@/api/types";

export interface QueuedScan {
  id: string;
  taskId: string;
  payload: ScanIn;
  queuedAt: string;
}

const STORAGE_KEY = "opsgrids.worker.scanQueue";

function readAll(): QueuedScan[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as QueuedScan[]) : [];
  } catch {
    return [];
  }
}

function writeAll(items: QueuedScan[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {
    // Storage unavailable or full (private browsing, a very old device) — the caller's own
    // online submit still works the ordinary way; only the offline fallback is degraded, and
    // silently rather than with a banner nobody queued anything to explain.
  }
}

function makeId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Every scan still waiting to sync, for one task, oldest first. */
export function queuedScansForTask(taskId: string): QueuedScan[] {
  return readAll().filter((entry) => entry.taskId === taskId);
}

export function enqueueScan(taskId: string, payload: ScanIn): QueuedScan {
  const entry: QueuedScan = { id: makeId(), taskId, payload, queuedAt: new Date().toISOString() };
  writeAll([...readAll(), entry]);
  return entry;
}

export function dequeueScan(id: string): void {
  writeAll(readAll().filter((entry) => entry.id !== id));
}
