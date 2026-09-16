/**
 * §2.10 — a decided request has three facts (`status`, `application_state`, the record's own
 * status), and confusing them is the failure this section exists to prevent. These pin the four
 * combinations the detail header and "My requests" must render, plus the silent cases.
 */

import { describe, expect, it } from "vitest";

import {
  applicationOutcomeCopy,
  detailStatusCopy,
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
