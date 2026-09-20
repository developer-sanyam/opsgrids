/**
 * §2.10 — a decided request has three facts (`status`, `application_state`, the record's own
 * status), and confusing them is the failure this section exists to prevent. These pin the four
 * combinations the detail header and "My requests" must render, plus the silent cases.
 */

import { describe, expect, it } from "vitest";

import {
  applicationOutcomeCopy,
  detailStatusCopy,
  dueCellCopy,
  dueLineCopy,
  isOverdue,
  inboxReasonChip,
  myRequestStatusCopy,
} from "./approvalCopy";

describe("detailStatusCopy — the request detail header pill", () => {
  it("approved, applied: plain 'Approved' — applied is silent", () => {
    expect(detailStatusCopy("approved", "applied", null)).toEqual({
      label: "Approved",
      tone: "positive",
    });
  });

  it("approved, not yet reported (null): still plain 'Approved', no 'applying…' state", () => {
    expect(detailStatusCopy("approved", null, null)).toEqual({
      label: "Approved",
      tone: "positive",
    });
  });

  it("approved, failed: 'Approved — not applied', warning tone — the decision still stands", () => {
    expect(detailStatusCopy("approved", "failed", null)).toEqual({
      label: "Approved — not applied",
      tone: "warning",
    });
  });

  it("rejected, applied: plain 'Rejected'", () => {
    expect(detailStatusCopy("rejected", "applied", null)).toEqual({
      label: "Rejected",
      tone: "negative",
    });
  });

  it("rejected, failed: 'Rejected — not applied', warning tone", () => {
    expect(detailStatusCopy("rejected", "failed", null)).toEqual({
      label: "Rejected — not applied",
      tone: "warning",
    });
  });

  it("pending, with a named approver: 'Waiting on {Name}'", () => {
    expect(detailStatusCopy("pending", null, "Priya Nair")).toEqual({
      label: "Waiting on Priya Nair",
      tone: "neutral",
    });
  });

  it("pending, no approver: 'Unassigned' — not 'Waiting to be routed', which is My requests' copy", () => {
    expect(detailStatusCopy("pending", null, null)).toEqual({
      label: "Unassigned",
      tone: "warning",
    });
  });
});

describe("myRequestStatusCopy — the requester's own list", () => {
  it("pending, no approver yet: 'Waiting to be routed'", () => {
    expect(myRequestStatusCopy("pending", null, null)).toEqual({
      label: "Waiting to be routed",
      tone: "neutral",
    });
  });

  it("shares the same four decided combinations as the detail pill", () => {
    expect(myRequestStatusCopy("approved", "failed", null).label).toBe("Approved — not applied");
    expect(myRequestStatusCopy("rejected", "failed", null).label).toBe("Rejected — not applied");
  });
});

describe("applicationOutcomeCopy — the inline alert body, only when application_state = 'failed'", () => {
  it("approved but not applied: points at HR, never says the approval failed", () => {
    const copy = applicationOutcomeCopy("approved", "The record has since moved.");
    expect(copy?.title).toBe("Approved, but the correction couldn't be applied.");
    expect(copy?.body).toBe("The record has since moved.");
    expect(copy?.instruction).toBe("The day wasn't changed. Ask HR to correct it directly.");
  });

  it("rejected but not applied: the record disagrees with the request, and says so", () => {
    const copy = applicationOutcomeCopy("rejected", "Could not update the record.");
    expect(copy?.title).toBe("Rejected, but the record wasn't updated.");
    expect(copy?.instruction).toBe(
      "The day is unchanged. Ask HR to close the request on the record.",
    );
  });
});

describe("inboxReasonChip — §2.3's six reasons, slice 4's two escalation chips included", () => {
  it("escalated_to_me: names who it moved up from", () => {
    expect(inboxReasonChip("escalated_to_me", "Priya Nair")).toEqual({
      label: "Escalated to you from Priya Nair",
      tone: "neutral",
    });
  });

  it("escalated_to_me with no from_name: drops the name, keeps the chip", () => {
    expect(inboxReasonChip("escalated_to_me", null)).toEqual({
      label: "Escalated to you",
      tone: "neutral",
    });
  });

  it("escalated_away: names who it is now with, for the approver who remains eligible", () => {
    expect(inboxReasonChip("escalated_away", "Marcus Feng")).toEqual({
      label: "Still yours — now with Marcus Feng",
      tone: "neutral",
    });
  });

  it("escalated_away with no from_name: still reads as 'still yours'", () => {
    expect(inboxReasonChip("escalated_away", null)).toEqual({
      label: "Still yours — someone else has it",
      tone: "neutral",
    });
  });
});

describe("dueLineCopy — slice 4, §2.11: the client never computes a due time, only formats one", () => {
  const formatAbsolute = () => "16 Sep, 9:12 am IST";
  const now = new Date("2026-09-16T00:00:00Z");

  it("null due_at renders the literal 'No due time' — never a placeholder, never invented", () => {
    expect(dueLineCopy(null, formatAbsolute, now)).toEqual({
      kind: "none",
      label: "No due time",
      tone: "neutral",
    });
  });

  it("a future due_at reads 'Due {absolute} · in {duration}'", () => {
    const dueAt = new Date(now.getTime() + 6 * 60 * 60 * 1000).toISOString();
    expect(dueLineCopy(dueAt, formatAbsolute, now)).toEqual({
      kind: "due",
      label: "Due 16 Sep, 9:12 am IST · in 6 hours",
      tone: "neutral",
    });
  });

  it("a past due_at reads 'Overdue by {duration}', never a negative duration", () => {
    const dueAt = new Date(now.getTime() - 6 * 60 * 60 * 1000).toISOString();
    expect(dueLineCopy(dueAt, formatAbsolute, now)).toEqual({
      kind: "overdue",
      label: "Overdue by 6 hours",
      tone: "negative",
    });
  });

  it("due exactly now reads as overdue, not due-in-zero", () => {
    expect(dueLineCopy(now.toISOString(), formatAbsolute, now)).toEqual({
      kind: "overdue",
      label: "Overdue by less than a minute",
      tone: "negative",
    });
  });
});

describe("dueCellCopy — §4.A's Due column, whose primary read is a duration", () => {
  const now = new Date("2026-09-16T00:00:00Z");
  const hours = (n: number) => new Date(now.getTime() + n * 60 * 60 * 1000).toISOString();

  it("renders 'No due time' for a null, which is a modelled state and not missing data", () => {
    expect(dueCellCopy(null, now)).toEqual({ kind: "none", label: "No due time" });
  });

  it("renders a future deadline as 'Due in {duration}'", () => {
    expect(dueCellCopy(hours(6), now)).toEqual({ kind: "due", label: "Due in 6 hours" });
  });

  it("renders a past deadline as 'Overdue by {duration}'", () => {
    expect(dueCellCopy(hours(-6), now)).toEqual({ kind: "overdue", label: "Overdue by 6 hours" });
  });

  it("treats an unparseable value as no deadline rather than guessing one", () => {
    expect(dueCellCopy("not-an-instant", now)).toEqual({ kind: "none", label: "No due time" });
  });
});

describe("isOverdue — §4.A's Overdue filter", () => {
  const now = new Date("2026-09-16T00:00:00Z");

  it("is true only for a deadline the clock has passed", () => {
    expect(isOverdue(new Date(now.getTime() - 60_000).toISOString(), now)).toBe(true);
    expect(isOverdue(new Date(now.getTime() + 60_000).toISOString(), now)).toBe(false);
  });

  it("is false for a row with no deadline — 'no deadline' is never 'overdue'", () => {
    expect(isOverdue(null, now)).toBe(false);
  });
});
