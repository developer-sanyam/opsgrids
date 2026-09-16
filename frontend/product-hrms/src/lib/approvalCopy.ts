/**
 * The approved copy for every approval surface (screens spec §1.1, §2.3, §2.10), in one place so
 * two screens never say the same fact two different ways. Nothing here is paraphrased — every
 * string is quoted from the spec.
 */

import type { TagTone } from "@opsgrids/ui";

import type {
  ApplicationState,
  AttendanceStatus,
  InboxReason,
  RegularizationSummary,
  RequestDetailOut,
  RequestStatus,
} from "@/api/types";
import { attendanceStatusLabel } from "@/lib/format";

/** §2.3 — the six `inbox_reason` values, mapped to a chip. The server's value is the gate; this
 *  never re-derives a reason, only renders the one it was given. */
export function inboxReasonChip(
  reason: InboxReason | null,
  fromName: string | null,
): { label: string; tone: TagTone } | null {
  switch (reason) {
    case "assigned":
      return { label: "Assigned to you", tone: "neutral" };
    case "delegated_to_me":
      return {
        label: fromName ? `Delegated from ${fromName}` : "Delegated to you",
        tone: "neutral",
      };
    case "escalated_to_me":
      return {
        label: fromName ? `Escalated to you from ${fromName}` : "Escalated to you",
        tone: "neutral",
      };
    case "delegated_away":
      return {
        label: fromName
          ? `Still yours — ${fromName} is standing in`
          : "Still yours — someone else has it",
        tone: "neutral",
      };
    case "escalated_away":
      return {
        label: fromName ? `Still yours — now with ${fromName}` : "Still yours — someone else has it",
        tone: "neutral",
      };
    case "unassigned":
      return { label: "Unassigned — no approver found", tone: "warning" };
    default:
      // An unrecognised value: no chip, but the row stays operable (§2.3).
      return null;
  }
}

/** §4.A / §4.D — `12 Sep · Absent → Regularised`. */
export function summaryLine(summary: RegularizationSummary): string {
  const date = new Date(`${summary.date}T00:00:00Z`);
  const day = date.toLocaleDateString("en-US", { day: "numeric", month: "short", timeZone: "UTC" });
  return `${day} · ${attendanceStatusLabel(summary.from as AttendanceStatus)} → ${attendanceStatusLabel(summary.to as AttendanceStatus)}`;
}

type StatusCopy = { label: string; tone: "neutral" | "positive" | "negative" | "warning" };

/** The decided half of the status pill — shared by the detail header (§4.B) and "My requests"
 *  (§4.D), which use exactly the same words once a request is no longer pending. */
function decidedStatusCopy(status: RequestStatus, applicationState: ApplicationState): StatusCopy {
  if (status === "approved") {
    // "applied" is silent — the request's own status already says what happened (§2.10).
    return applicationState === "failed"
      ? { label: "Approved — not applied", tone: "warning" }
      : { label: "Approved", tone: "positive" };
  }
  return applicationState === "failed"
    ? { label: "Rejected — not applied", tone: "warning" }
    : { label: "Rejected", tone: "negative" };
}

/** §4.B — the request detail header's status pill. Pending + no approver reads **"Unassigned"**,
 *  not "Waiting to be routed" (that copy is `myRequestStatusCopy`'s — the two screens say the
 *  same fact differently on purpose: a decider is told the queue it's in, a requester is told
 *  what's happening to their ask). */
export function detailStatusCopy(
  status: RequestStatus,
  applicationState: ApplicationState,
  approverName: string | null,
): StatusCopy {
  if (status === "pending") {
    return approverName
      ? { label: `Waiting on ${approverName}`, tone: "neutral" }
      : { label: "Unassigned", tone: "warning" };
  }
  return decidedStatusCopy(status, applicationState);
}

/** §4.D — "My requests"' status column. */
export function myRequestStatusCopy(
  status: RequestStatus,
  applicationState: ApplicationState,
  approverName: string | null,
): StatusCopy {
  if (status === "pending") {
    return approverName
      ? { label: `Waiting on ${approverName}`, tone: "neutral" }
      : { label: "Waiting to be routed", tone: "neutral" };
  }
  return decidedStatusCopy(status, applicationState);
}

/** §2.10 — the inline alert body when `application_state === "failed"`, split by outcome. Never
 *  shown when `application_state` is `null` or `"applied"` (silent, per the same section). */
export function applicationOutcomeCopy(
  status: RequestStatus,
  failureReason: string | null,
): { title: string; body: string; instruction: string } | null {
  if (status === "approved") {
    return {
      title: "Approved, but the correction couldn't be applied.",
      body: failureReason ?? "",
      instruction: "The day wasn't changed. Ask HR to correct it directly.",
    };
  }
  return {
    title: "Rejected, but the record wasn't updated.",
    body: failureReason ?? "",
    instruction: "The day is unchanged. Ask HR to close the request on the record.",
  };
}

/** §4.C — the attendance row's own "Couldn't be applied" title text, always naming Approved
 *  first: the action kept its name, the failure is appended (§2.10). */
export const CORRECTION_APPLICATION_FAILED_TITLE = "Couldn't be applied";

/** §4.B — one history entry: a tone, a sentence, an instant. `Timeline` renders the list. */
export interface HistoryEntry {
  id: string;
  tone: "neutral" | "positive" | "negative" | "late";
  content: string;
  quote?: string;
  timestamp: string;
  dateTime: string;
}

function openedSentence(
  step: RequestDetailOut["steps"][number],
  requesterName: string,
): string {
  const nameOrFallback = (n: string | null) => n ?? "Employee record unavailable";
  switch (step.reason) {
    case "unassigned":
      return `No approver was found in ${requesterName}'s reporting line, so this went to HR.`;
    case "delegated":
      return step.level === 1
        ? `Sent to ${nameOrFallback(step.approver_name)}, standing in for ${nameOrFallback(step.acting_for_name)}.`
        : `Moved to ${nameOrFallback(step.approver_name)}, standing in for ${nameOrFallback(step.acting_for_name)}.`;
    case "escalated":
      return step.approver_employee_id
        ? `Nobody answered in time, so this moved up to ${nameOrFallback(step.approver_name)}.`
        : "Nobody answered in time and there was no one further up, so this went to HR.";
    default: // "assigned"
      return `Sent to ${nameOrFallback(step.approver_name)} for approval.`;
  }
}

function decidedSentence(step: RequestDetailOut["steps"][number]): string {
  const approver = step.approver_name ?? "Employee record unavailable";
  const verb = step.outcome === "approved" ? "approved" : "rejected";
  return step.acting_for_employee_id
    ? `${approver} ${verb} this, standing in for ${step.acting_for_name ?? "Employee record unavailable"}.`
    : `${approver} ${verb} this.`;
}

/**
 * §4.B — the whole history, chronological. The last two "applied"/"failed" entries are derived
 * from `application_state` and are never steps (`approval_steps` is routing history only, §2.10);
 * nothing is rendered while `application_state` is null — there is no "waiting to be applied"
 * entry.
 */
export function buildHistoryEntries(
  detail: RequestDetailOut,
  formatAt: (iso: string) => { text: string; dateTime: string },
): HistoryEntry[] {
  const requesterName = detail.requester_name ?? "This employee";
  const entries: HistoryEntry[] = [];

  for (const step of detail.steps) {
    const opened = formatAt(step.opened_at);
    entries.push({
      id: `${step.level}-open`,
      tone: step.reason === "escalated" ? "late" : "neutral",
      content: openedSentence(step, requesterName),
      timestamp: opened.text,
      dateTime: opened.dateTime,
    });
    if (step.outcome === "approved" || step.outcome === "rejected") {
      const closed = formatAt(step.closed_at ?? step.opened_at);
      const entry: HistoryEntry = {
        id: `${step.level}-decision`,
        tone: step.outcome === "approved" ? "positive" : "negative",
        content: decidedSentence(step),
        timestamp: closed.text,
        dateTime: closed.dateTime,
      };
      if (detail.comment) entry.quote = detail.comment;
      entries.push(entry);
    }
  }

  if (detail.application_state && detail.applied_at) {
    const applied = formatAt(detail.applied_at);
    if (detail.application_state === "applied") {
      if (detail.status === "approved") {
        entries.push({
          id: "applied",
          tone: "positive",
          content: `${requesterName}'s attendance was corrected.`,
          timestamp: applied.text,
          dateTime: applied.dateTime,
        });
      }
      // A landed rejection gets no entry — the decision entry above already said it (§2.10).
    } else {
      entries.push({
        id: "failed",
        tone: "negative",
        content:
          detail.status === "approved"
            ? `The correction couldn't be applied: ${detail.application_failure_reason ?? ""}`
            : `The record wasn't updated: ${detail.application_failure_reason ?? ""}`,
        timestamp: applied.text,
        dateTime: applied.dateTime,
      });
    }
  }

  return entries;
}
