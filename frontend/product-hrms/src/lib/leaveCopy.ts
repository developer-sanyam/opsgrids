/**
 * Copy for the leave screens, in one place so the balance card, the request table and the
 * cancel dialog never say the same fact two different ways — the same discipline
 * `approvalCopy.ts` keeps for the approvals surface.
 */

import type { TagTone } from "@opsgrids/ui";

import type { LeaveRequestStatus } from "@/api/types";

type StatusCopy = { label: string; tone: TagTone };

/** The leave request list's status column and the detail header's pill. */
export function leaveStatusCopy(status: LeaveRequestStatus): StatusCopy {
  switch (status) {
    case "pending":
      return { label: "Pending", tone: "neutral" };
    case "approved":
      return { label: "Approved", tone: "positive" };
    case "rejected":
      return { label: "Rejected", tone: "negative" };
    case "cancelled":
      return { label: "Cancelled", tone: "neutral" };
    case "failed":
      return { label: "Approved — not applied", tone: "warning" };
  }
}

/** `21 Sep` for a single day, `21 Sep – 22 Sep` for a range — the same date-range shape
 *  `approvalCopy.ts`'s own leave summary line uses, kept here too since the request table
 *  renders `start_date`/`end_date` directly rather than through a `summary` object. */
export function formatLeaveRange(startDate: string, endDate: string): string {
  const fmt = (iso: string) =>
    new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
      day: "numeric",
      month: "short",
      timeZone: "UTC",
    });
  return startDate === endDate ? fmt(startDate) : `${fmt(startDate)} – ${fmt(endDate)}`;
}

/** Half-days as a plain count and the whole-day equivalent — `4 half-days (2 days)`, or just
 *  `1 half-day` when it doesn't divide evenly cleaner shown as a fraction: `3 half-days (1.5 days)`. */
export function formatHalfDays(halfDays: number): string {
  const unit = Math.abs(halfDays) === 1 ? "half-day" : "half-days";
  const days = halfDays / 2;
  const daysLabel = Number.isInteger(days) ? String(days) : days.toFixed(1);
  return `${halfDays} ${unit} (${daysLabel} ${Math.abs(days) === 1 ? "day" : "days"})`;
}
