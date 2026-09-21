/**
 * Formatting helpers for OpsGrids Revenue. Money is always passed as an integer count of **minor
 * units** (paise for INR) plus an ISO currency — these are the only place minor units become a
 * human string, so the rest of the app never divides by 100 by hand.
 */

import { USE_FIXTURES } from "@/api/client";

/** Fixtures are dated around this fixed instant so relative times read sensibly offline — never
 *  reached live, where `nowIso()` returns the real clock instead. */
const FIXTURE_NOW_ISO = "2026-08-24T18:30:00Z";

/**
 * The app's current instant. The one place "now" is read from, so a frozen fixture clock never
 * leaks onto live data: fixture mode gets the fixed instant fixtures were dated around, a real
 * build gets `Date.now()`. Call it at the point you need "now", not once at module load — a
 * `const` computed on import stays stuck at the moment the tab was opened for as long as the
 * session lives.
 */
export function nowIso(): string {
  return USE_FIXTURES ? FIXTURE_NOW_ISO : new Date().toISOString();
}

const CURRENCY_SYMBOL: Record<string, string> = {
  INR: "₹",
  USD: "$",
  EUR: "€",
  GBP: "£",
};

function symbolFor(currency: string): string {
  return CURRENCY_SYMBOL[currency] ?? `${currency} `;
}

/**
 * Full money, grouped in the Indian system: `12,50,00,000` minor units of INR -> "₹12,50,00,000".
 * No fractional paise — deal values are whole rupees.
 */
export function formatMoney(minor: number, currency = "INR"): string {
  const major = minor / 100;
  if (currency === "INR") {
    return `₹${Math.round(major).toLocaleString("en-IN")}`;
  }
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(major);
  } catch {
    return `${symbolFor(currency)}${Math.round(major).toLocaleString("en-IN")}`;
  }
}

/** Drop a trailing ".0" from a one-decimal string: "3.0" -> "3", "3.2" -> "3.2". */
function trim(n: number): string {
  return n.toFixed(1).replace(/\.0$/, "");
}

/**
 * Compact money for tight surfaces (KPIs, kanban cards), using Indian crore/lakh:
 * `32,00,00,000` minor INR -> "₹3.2 Cr"; `95,00,000` minor -> "₹95 L".
 */
export function formatMoneyCompact(minor: number, currency = "INR"): string {
  const major = minor / 100;
  const sym = symbolFor(currency);
  if (currency === "INR") {
    if (major >= 1e7) return `${sym}${trim(major / 1e7)} Cr`;
    if (major >= 1e5) return `${sym}${trim(major / 1e5)} L`;
    return `${sym}${Math.round(major).toLocaleString("en-IN")}`;
  }
  if (major >= 1e9) return `${sym}${trim(major / 1e9)}B`;
  if (major >= 1e6) return `${sym}${trim(major / 1e6)}M`;
  if (major >= 1e3) return `${sym}${trim(major / 1e3)}K`;
  return `${sym}${Math.round(major)}`;
}

/** "2026-09-30" -> "30 Sep 2026". Returns "—" for a missing date. */
export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** "2026-08-22T11:20:00Z" -> "22 Aug, 4:50 PM" (times shown in the org's IST wall clock). */
export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const date = d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "Asia/Kolkata" });
  const time = d.toLocaleTimeString("en-IN", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "Asia/Kolkata",
  });
  return `${date}, ${time}`;
}

/**
 * A relative timestamp against `now` (default the app's fixed NOW): "just now", "3 hr ago",
 * "Yesterday", "5 days ago", then an absolute date past a week.
 */
export function formatRelative(iso: string | null, now: string = nowIso()): string {
  if (!iso) return "—";
  const then = Date.parse(iso);
  const ref = Date.parse(now);
  const mins = Math.round((ref - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  const days = Math.round(hrs / 24);
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  return formatDate(iso);
}

/** Initials for an avatar fallback: "Sofia Marchetti" -> "SM". */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}
