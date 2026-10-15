/**
 * The sentences a person reads when a reveal does not show a number. Every one names what
 * happened and what to do next, and none of them is the server's log line.
 */
import { describe, expect, it } from "vitest";

import { ApiError } from "../http";
import { nothingOnFile, revealFailure } from "./revealMessages";

function refusal(status: number, detail: string | undefined): ApiError {
  return new ApiError(status, detail ?? `Request failed with ${status}`, {
    status,
    ...(detail !== undefined ? { detail } : {}),
  });
}

describe("revealFailure", () => {
  it.each([
    ["lead", "This lead is not assigned to you, so you cannot reveal its details."],
    ["contact", "This contact is not assigned to you, so you cannot reveal its details."],
  ] as const)("says a %s out of someone's scope is not theirs to reveal", (subject, text) => {
    expect(revealFailure(refusal(403, "Out of scope for this resource"), subject)).toEqual({
      tone: "error",
      text,
    });
  });

  it("says a missing permission in words, and who to ask", () => {
    expect(revealFailure(refusal(403, "Missing permission: crm.lead.lead.read_sensitive"), "lead")).toEqual({
      tone: "error",
      text: "You don't have permission to reveal this lead's details. Ask an administrator for access.",
    });
  });

  it("gives any other 403 the permission sentence: whatever the server refused on, the person cannot do this", () => {
    expect(
      revealFailure(refusal(403, "crm.lead.lead.read_sensitive requires ORG scope; this grant is OWN"), "lead").text,
    ).toBe("You don't have permission to reveal this lead's details. Ask an administrator for access.");
  });

  it("says a plan that does not include it", () => {
    expect(revealFailure(refusal(402, "Payment required"), "lead").text).toBe(
      "Revealing details isn't part of your plan.",
    );
  });

  it.each([
    ["No phone number is on file for this lead", "No phone number is on file for this lead."],
    ["No email address is on file for this lead", "No email address is on file for this lead."],
  ])("shows the server's own sentence for nothing on file, finished, and as a neutral answer: %s", (detail, text) => {
    expect(revealFailure(refusal(404, detail), "lead")).toEqual({ tone: "neutral", text });
  });

  it("shows the server's sentence for a lead that is not in the organization, as a problem", () => {
    expect(revealFailure(refusal(404, "That lead is not part of this organization"), "lead")).toEqual({
      tone: "error",
      text: "That lead is not part of this organization.",
    });
  });

  it("does not add a second full stop to a sentence that has one", () => {
    expect(revealFailure(refusal(404, "That lead is gone."), "lead").text).toBe("That lead is gone.");
  });

  it("shows the server's sentence for anything else it says", () => {
    expect(revealFailure(refusal(422, "Request validation failed"), "lead")).toEqual({
      tone: "error",
      text: "Request validation failed.",
    });
  });

  it("does not show the machinery of a dropped connection", () => {
    expect(revealFailure(new TypeError("Failed to fetch"), "lead")).toEqual({
      tone: "error",
      text: "Couldn't reveal this right now. Try again.",
    });
  });
});

describe("nothingOnFile", () => {
  it("names what is missing, as a neutral answer", () => {
    expect(nothingOnFile("phone number")).toEqual({ tone: "neutral", text: "No phone number is on file." });
  });
});
