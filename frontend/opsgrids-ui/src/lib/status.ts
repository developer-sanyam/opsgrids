/**
 * The status vocabulary: one map from a status word to one of five tones, for every product.
 *
 * The owner brief (§11) asks that the same status always looks the same. Before this, twelve local
 * pills each chose their own colour, so "Pending" was blue in Operations and amber in Support,
 * "High" amber in Support and red in PulseBoard, "Cancelled" red, grey or amber depending on the
 * app. Here a word has exactly one tone, and a product that shows the word gets that tone.
 *
 * - The tones are closed. A product word that is not listed ("Awaiting DNS", "Half day") is shown
 *   with `<StatusBadge tone="warning" label="…" />`, choosing one of these five — never a sixth
 *   colour.
 * - A word whose meaning genuinely differs between products does not belong here; the products
 *   map it to a tone themselves. A word listed here means the same thing everywhere it is used.
 * - Where the mockups show a status, they decide: Trial is amber and Cancelled red (mock-1,
 *   mock-4); priority High red, Medium amber, Low blue (mock-4, mock-5).
 */

/** The five status tones. Each maps onto a token family in console.css (see StatusBadge). */
export type StatusTone = "success" | "warning" | "danger" | "info" | "neutral";

export const STATUS_TONES: readonly StatusTone[] = ["success", "warning", "danger", "info", "neutral"];

const REGISTRY = {
  // success — done, healthy, in good standing.
  active: "success",
  approved: "success",
  accepted: "success",
  complete: "success",
  completed: "success",
  connected: "success",
  converted: "success",
  delivered: "success",
  done: "success",
  filed: "success",
  fulfilled: "success",
  healthy: "success",
  live: "success",
  met: "success",
  on_track: "success",
  operational: "success",
  paid: "success",
  present: "success",
  published: "success",
  received: "success",
  resolved: "success",
  solved: "success",
  verified: "success",
  won: "success",

  // warning — waiting on someone, provisional, or at risk.
  at_risk: "warning",
  awaiting_approval: "warning",
  degraded: "warning",
  due_soon: "warning",
  medium: "warning",
  on_hold: "warning",
  on_notice: "warning",
  partially_paid: "warning",
  // In dunning: the subscription still runs, and the payment is late. `overdue` is the money.
  past_due: "warning",
  paused: "warning",
  pending: "warning",
  trial: "warning",

  // info — in motion, or new and not yet touched.
  comp: "info",
  in_progress: "info",
  in_review: "info",
  invited: "info",
  low: "info",
  new: "info",
  open: "info",
  processing: "info",
  queued: "info",
  scheduled: "info",
  sending: "info",
  // Handed over, outcome not yet known: an invoice sent is not yet paid.
  sent: "info",
  submitted: "info",

  // danger — failed, stopped, late, or needing action now.
  absent: "danger",
  blocked: "danger",
  bounced: "danger",
  breached: "danger",
  canceled: "danger",
  cancelled: "danger",
  critical: "danger",
  declined: "danger",
  delayed: "danger",
  disconnected: "danger",
  error: "danger",
  failed: "danger",
  high: "danger",
  locked: "danger",
  lost: "danger",
  off_track: "danger",
  outage: "danger",
  overdue: "danger",
  rejected: "danger",
  suspended: "danger",
  urgent: "danger",

  // neutral — not live: switched off, finished with, or not started.
  archived: "neutral",
  churned: "neutral",
  closed: "neutral",
  disabled: "neutral",
  disqualified: "neutral",
  dormant: "neutral",
  draft: "neutral",
  ended: "neutral",
  exited: "neutral",
  expired: "neutral",
  inactive: "neutral",
  not_required: "neutral",
  retired: "neutral",
  skipped: "neutral",
  unsubscribed: "neutral",
  void: "neutral",
  withdrawn: "neutral",
} as const satisfies Record<string, StatusTone>;

/** A status word the registry knows, in its canonical form: lower case, words joined by `_`. */
export type StatusWord = keyof typeof REGISTRY;

/** Every registered word, in the order above — for a workshop story or a test to walk. */
export const STATUS_WORDS = Object.keys(REGISTRY) as StatusWord[];

/** `word` is a registered word exactly as written (use `statusTone` for loose input). */
export function isStatusWord(word: string): word is StatusWord {
  return Object.hasOwn(REGISTRY, word);
}

/** `"On hold"`, `"on-hold"`, `"ON_HOLD"` → `"on_hold"`. */
function canonical(word: string): string {
  return word.trim().toLowerCase().replace(/[\s-]+/g, "_");
}

/**
 * The tone for a status word as an API or a person wrote it, or `undefined` when the registry
 * does not know it — in which case the product chooses a tone for its own word. A `StatusWord`
 * always has one.
 */
export function statusTone(word: StatusWord): StatusTone;
export function statusTone(word: string): StatusTone | undefined;
export function statusTone(word: string): StatusTone | undefined {
  const key = canonical(word);
  return isStatusWord(key) ? REGISTRY[key] : undefined;
}

/** `"on_hold"` → `"On hold"`: the default label when a product does not pass its own words. */
export function statusLabel(word: StatusWord): string {
  const spaced = word.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
