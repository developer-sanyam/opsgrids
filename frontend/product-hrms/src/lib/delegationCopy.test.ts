/**
 * §4.E's copy, unit-tested directly: the row sentence in both modes, the revoke body's honest
 * "never re-routes" wording, the repoint line's C4 rule (rendered only when `repointed_count` is
 * greater than zero, never "0 requests moved"), and the create/revoke announcements.
 */

import { describe, expect, it } from "vitest";

import type { DelegationOut } from "@/api/types";
import {
  delegationConsequenceNote,
  delegationCreatedAnnouncement,
  delegationRevokedAnnouncement,
  delegationSentence,
  delegationStatusTag,
  repointedLine,
  revokeConfirmationBody,
} from "./delegationCopy";

const BASE: DelegationOut = {
  id: "del-1",
  delegator_employee_id: "emp-sneha",
  delegator_name: "Sneha Kulkarni",
  delegator_location_code: "Bengaluru",
  delegate_employee_id: "emp-rohan",
  delegate_name: "Rohan Mehta",
  starts_at: "2026-09-15T03:30:00Z",
  ends_at: "2026-09-22T03:30:00Z",
  status: "active",
  created_by_employee_id: "emp-sneha",
  revoked_at: null,
  revoked_by_employee_id: null,
};

describe("delegationStatusTag", () => {
  it("maps every status to a label and tone", () => {
    expect(delegationStatusTag("active")).toEqual({ label: "Active", tone: "positive" });
    expect(delegationStatusTag("scheduled")).toEqual({ label: "Scheduled", tone: "info" });
    expect(delegationStatusTag("ended")).toEqual({ label: "Ended", tone: "neutral" });
    expect(delegationStatusTag("revoked")).toEqual({ label: "Revoked", tone: "neutral" });
  });
});

describe("delegationSentence", () => {
  it("reads 'decides for you' when the caller is the delegator", () => {
    expect(delegationSentence(BASE, "emp-sneha")).toBe("Rohan Mehta decides for you");
  });

  it("names the delegator in ORG mode, for someone else's delegation", () => {
    expect(delegationSentence(BASE, "emp-marcus")).toBe("Rohan Mehta decides for Sneha Kulkarni");
  });

  it("falls back to the PersonRef fallback text for an unresolved delegate name", () => {
    expect(delegationSentence({ ...BASE, delegate_name: null }, "emp-sneha")).toBe(
      "Employee record unavailable decides for you",
    );
  });
});

describe("revokeConfirmationBody", () => {
  it("says the delegation stays 'yours' when the caller is the delegator", () => {
    expect(revokeConfirmationBody(BASE, "emp-sneha")).toBe(
      "Rohan Mehta will stop being able to decide for you immediately. Anything already with them stays with them and is still yours to decide. This can't be undone.",
    );
  });

  it("never implies anything comes back to the caller — third person in ORG mode", () => {
    const body = revokeConfirmationBody(BASE, "emp-marcus");
    expect(body).toContain("decide for Sneha Kulkarni immediately");
    expect(body).toContain("still Sneha Kulkarni's to decide");
    expect(body).not.toContain("yours");
  });
});

describe("repointedLine — C4: rendered only when repointed_count > 0", () => {
  it("is null at 0 — never '0 requests moved'", () => {
    expect(repointedLine("Rohan Mehta", 0)).toBeNull();
  });

  it("is singular at 1", () => {
    expect(repointedLine("Rohan Mehta", 1)).toBe("1 request waiting on you moved to Rohan Mehta.");
  });

  it("is plural above 1", () => {
    expect(repointedLine("Rohan Mehta", 3)).toBe("3 requests waiting on you moved to Rohan Mehta.");
  });
});

describe("delegationConsequenceNote", () => {
  it("names the immediate re-point when the delegation starts now", () => {
    expect(delegationConsequenceNote(true, "15 Sep 2026")).toBe(
      "Anything already waiting on you moves to them straight away. The due times don't change.",
    );
  });

  it("names the future start instead, with no re-point promise", () => {
    expect(delegationConsequenceNote(false, "20 Sep 2026")).toBe(
      "This starts on 20 Sep 2026. Anything waiting on you now stays with you until then.",
    );
  });
});

describe("delegationCreatedAnnouncement", () => {
  it("says 'set up' for an immediate start, naming the delegate and 'you'", () => {
    expect(
      delegationCreatedAnnouncement("Rohan Mehta", true, null, true, "15 Sep, 9:00 am IST"),
    ).toBe("Delegation set up. Rohan Mehta decides for you from 15 Sep, 9:00 am IST.");
  });

  it("says 'scheduled' for a future start", () => {
    expect(
      delegationCreatedAnnouncement("Rohan Mehta", true, null, false, "20 Sep, 9:00 am IST"),
    ).toBe("Delegation scheduled. Rohan Mehta decides for you from 20 Sep, 9:00 am IST.");
  });

  it("names the delegator instead of 'you' in ORG mode", () => {
    expect(
      delegationCreatedAnnouncement(
        "Rohan Mehta",
        false,
        "Priya Nair",
        true,
        "15 Sep, 9:00 am IST",
      ),
    ).toBe("Delegation set up. Rohan Mehta decides for Priya Nair from 15 Sep, 9:00 am IST.");
  });
});

describe("delegationRevokedAnnouncement", () => {
  it("says 'for you' in the ordinary case", () => {
    expect(delegationRevokedAnnouncement("Rohan Mehta", true, null)).toBe(
      "Delegation revoked. Rohan Mehta can no longer decide for you.",
    );
  });

  it("names the delegator in ORG mode", () => {
    expect(delegationRevokedAnnouncement("Rohan Mehta", false, "Priya Nair")).toBe(
      "Delegation revoked. Rohan Mehta can no longer decide for Priya Nair.",
    );
  });
});
