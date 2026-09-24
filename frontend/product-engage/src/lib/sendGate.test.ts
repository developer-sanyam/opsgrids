import { describe, expect, it } from "vitest";

import { ApiError } from "@opsgrids/client";

import { explainSendFailure } from "./sendGate";

/**
 * Every sentence `services.request_send` and `_check_public_base` can raise, copied from the
 * service, and the shortcut each one earns. The sentences are the service's and are shown
 * verbatim; if the service rewords one, the shortcut is what silently disappears — so it is pinned
 * here, where the reword fails a test instead.
 */

function refused(message: string) {
  return new ApiError(422, message, { type: "business-rule", title: "Unprocessable", status: 422, detail: message });
}

describe("a 422 from the send gate", () => {
  const CASES: { message: string; next: unknown; hint?: boolean }[] = [
    {
      message: "Add a subject before sending.",
      next: { kind: "field", label: "Add a subject", field: "subject" },
    },
    {
      message: "Add a body before sending.",
      next: { kind: "field", label: "Write the body", field: "body" },
    },
    {
      message: "The campaign's sender no longer exists. Choose another.",
      next: { kind: "field", label: "Choose another sender", field: "sender" },
    },
    {
      message:
        "support@vaayu-logistics.example.com has not passed a test send. Verify the sender before sending.",
      next: { kind: "link", label: "Open Senders", to: "/senders" },
    },
    {
      message:
        "The domain behind support@vaayu-logistics.example.com is no longer verified. Re-verify its DNS records before sending.",
      next: { kind: "link", label: "Open Domains", to: "/domains" },
    },
    {
      message: "Connect an email provider before sending a campaign.",
      next: { kind: "link", label: "Open Connections", to: "/connection" },
    },
    {
      message: "The campaign's list no longer exists.",
      next: { kind: "field", label: "Choose another list", field: "list" },
    },
    {
      message: "You have no contacts who can be emailed.",
      next: { kind: "link", label: "Open Contacts", to: "/contacts" },
    },
    {
      message: "This list has no contacts who can be emailed.",
      next: { kind: "link", label: "Open Contacts", to: "/contacts" },
    },
    {
      message:
        "Unsubscribe links are not configured for this environment, so campaigns cannot be sent.",
      next: null,
      hint: true,
    },
    {
      message:
        "ENGAGE_PUBLIC_API_BASE is not a public https address in this environment, so campaigns cannot be sent.",
      next: null,
      hint: true,
    },
  ];

  it.each(CASES)("shows “$message” verbatim, with its shortcut", ({ message, next, hint }) => {
    const reason = explainSendFailure(refused(message));
    expect(reason.tone).toBe("warning");
    expect(reason.title).toBe("Not ready to send");
    expect(reason.message).toBe(message);
    expect(reason.next).toEqual(next);
    expect(reason.hint !== null).toBe(hint === true);
  });

  it("offers no shortcut for a sentence it does not recognise, and still shows it", () => {
    const reason = explainSendFailure(refused("Something the service added next month."));
    expect(reason.message).toBe("Something the service added next month.");
    expect(reason.next).toBeNull();
    expect(reason.hint).toBeNull();
  });
});

describe("the other refusals", () => {
  it("names Engage not being active on a 402 and sends the customer to the dashboard", () => {
    const reason = explainSendFailure(new ApiError(402, "engage is not active", null));
    expect(reason.title).toBe("Engage isn't active for this organization");
    expect(reason.tone).toBe("danger");
    expect(reason.next).toEqual({
      kind: "link",
      label: "Open Products in the dashboard",
      to: "https://app.opsgrids.com/",
      external: true,
    });
    // The gateway's lower-case wording is not what the customer reads.
    expect(reason.message).not.toContain("engage is not active");
  });

  it("says sending has its own permission on a 403", () => {
    const reason = explainSendFailure(new ApiError(403, "You do not have engage.campaign.campaign.send", null));
    expect(reason.title).toBe("You can't send campaigns");
    expect(reason.message).toBe("You do not have engage.campaign.campaign.send");
    expect(reason.hint).toContain("Send a campaign");
  });

  it("keeps a 409 the service's own sentence", () => {
    const reason = explainSendFailure(new ApiError(409, "Only a draft can be sent; this campaign is sending", null));
    expect(reason.title).toBe("This campaign can't be sent again");
    expect(reason.message).toBe("Only a draft can be sent; this campaign is sending");
  });

  it("says nothing was sent when the request never reached the service", () => {
    const reason = explainSendFailure(new TypeError("Failed to fetch"));
    expect(reason.message).toContain("has not been sent");
    expect(reason.message).not.toContain("Failed to fetch");
  });

  it("keeps an unexpected status a plain failure", () => {
    const reason = explainSendFailure(new ApiError(500, "Internal Server Error", null));
    expect(reason.tone).toBe("danger");
    expect(reason.message).toBe("Internal Server Error");
    expect(reason.next).toBeNull();
  });
});
