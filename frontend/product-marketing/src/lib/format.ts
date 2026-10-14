import { formatMoney as formatMajorMoney, toMajor } from "@opsgrids/ui";

import type { CampaignStatus, Channel } from "@/api/types";

/**
 * Money is stored in integer minor units (paise for INR). Every formatter takes minor units and an
 * ISO 4217 currency and divides by the currency's own exponent (100 for INR, 1 for JPY, 1000 for
 * KWD) exactly once, here — the UI never does its own arithmetic on money.
 */

/** 12345678 paise, "INR" -> "₹1,23,457" — full grouped amount, no decimals. */
export function formatMoney(minor: number, currency: string): string {
  return formatMajorMoney(minor, currency);
}

/** 1420000000 paise, "INR" -> "₹1.4Cr" — a compact headline figure for KPI cards. */
export function formatMoneyCompact(minor: number, currency: string): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency,
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(toMajor(minor, currency));
}

/** The value a money input shows for an amount already stored: `125050` paise -> `"1250.50"`. */
export function minorToInput(minor: number): string {
  const sign = minor < 0 ? "-" : "";
  const absolute = Math.abs(minor);
  const whole = Math.trunc(absolute / 100);
  const cents = absolute % 100;
  return `${sign}${whole}.${String(cents).padStart(2, "0")}`;
}

export type MoneyParse = { ok: true; minor: number } | { ok: false; message: string };

/**
 * Read a major-unit amount typed into a form into integer minor units, on the string — never
 * `Math.round(parseFloat(x) * 100)`, which drifts once the amount is large enough. Spaces, commas
 * and a currency sign are tolerated (people paste amounts); anything past two decimal places is
 * refused rather than rounded away. Every money field on a campaign is `>= 0` server-side
 * (`budget_minor`, `spent_minor`, `revenue_minor`, `pipeline_minor`), so a negative amount is
 * refused here too, before it ever reaches the network. An empty field reads as zero — every one
 * of those fields defaults to `0` on the wire.
 */
export function parseMoneyToMinor(input: string): MoneyParse {
  const cleaned = input.trim().replace(/[\s,₹$€£]/g, "");
  if (cleaned === "") {
    return { ok: true, minor: 0 };
  }
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!match) {
    if (cleaned.startsWith("-")) {
      return { ok: false, message: "Enter an amount of zero or more." };
    }
    return {
      ok: false,
      message: "Enter a whole amount, like 24000 or 24000.50. No more than two decimal places.",
    };
  }
  const whole = match[1] ?? "0";
  const fraction = (match[2] ?? "").padEnd(2, "0");
  const minor = Number(whole) * 100 + Number(fraction);
  if (!Number.isSafeInteger(minor)) {
    return { ok: false, message: "That amount is too large." };
  }
  return { ok: true, minor };
}

/** A plain ROAS ratio -> "4.2×". A zero ratio (no spend) reads as an em dash. */
export function formatRoas(ratio: number): string {
  if (ratio === 0) return "—";
  return `${ratio.toFixed(1)}×`;
}

/** 15620 -> "15,620" with Indian digit grouping. */
export function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-IN").format(n);
}

/** "2026-03-15" -> "15 Mar 2026". Returns "—" for a null date (open-ended flight). */
export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}


const CHANNEL_LABELS: Record<Channel, string> = {
  email: "Email",
  social: "Social",
  search: "Search",
  events: "Events",
};

export function channelLabel(channel: Channel): string {
  return CHANNEL_LABELS[channel];
}

const STATUS_LABELS: Record<CampaignStatus, string> = {
  draft: "Draft",
  live: "Live",
  paused: "Paused",
  ended: "Ended",
};

export function campaignStatusLabel(status: CampaignStatus): string {
  return STATUS_LABELS[status];
}
