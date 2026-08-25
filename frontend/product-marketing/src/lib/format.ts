import type { CampaignStatus, Channel } from "@/api/types";

/**
 * Money is stored in integer minor units (paise for INR). Every formatter takes minor units and an
 * ISO 4217 currency and divides by 100 exactly once, here — the UI never does its own arithmetic
 * on money.
 */

/** 12345678 paise, "INR" -> "₹1,23,457" — full grouped amount, no decimals. */
export function formatMoney(minor: number, currency: string): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(minor / 100);
}

/** 1420000000 paise, "INR" -> "₹1.4Cr" — a compact headline figure for KPI cards. */
export function formatMoneyCompact(minor: number, currency: string): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency,
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(minor / 100);
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
