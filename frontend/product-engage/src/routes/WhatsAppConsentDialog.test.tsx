import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { ConsentEventOut, ContactOut, Page } from "@/api/types";
import { WhatsAppConsentDialog } from "./WhatsAppConsentDialog";

const CONTACT: ContactOut = {
  id: "c1a0f2e4-9b3d-4a7c-8e1f-2d6b9c4a5e10",
  email: "p•••@vaayu-logistics.example.com",
  name: "Priya Raghavan",
  attributes: {},
  created_at: "2026-08-29T09:18:02Z",
  updated_at: "2026-08-29T09:18:02Z",
};

function event(over: Partial<ConsentEventOut> = {}): ConsentEventOut {
  return {
    id: "e1",
    channel: "whatsapp",
    purpose: "marketing",
    status: "opted_in",
    source: "in_person",
    evidence: "trade fair",
    address: "+91•••••••210",
    contact_id: CONTACT.id,
    recorded_by: "u1",
    occurred_at: "2026-10-01T09:30:00Z",
    created_at: "2026-10-01T09:30:00Z",
    ...over,
  };
}

function page(items: ConsentEventOut[]): Page<ConsentEventOut> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length ? 1 : 0 };
}

function renderDialog(grants: string[] = ["engage.audience.contact.write:ORG"]) {
  const onChanged = vi.fn();
  render(
    <PermissionProvider grants={grants}>
      <WhatsAppConsentDialog contact={CONTACT} onClose={() => {}} onChanged={onChanged} />
    </PermissionProvider>,
  );
  return { onChanged };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("standing", () => {
  it("says plainly that nothing is on record", async () => {
    vi.spyOn(client, "listWhatsAppConsent").mockResolvedValue(page([]));
    renderDialog();
    expect(await screen.findByText(/No WhatsApp opt-in on record/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Withdraw opt-in" })).toBeNull();
  });

  it("shows the newest event, masked, and offers to withdraw an opt-in", async () => {
    vi.spyOn(client, "listWhatsAppConsent").mockResolvedValue(page([event()]));
    renderDialog();
    expect(await screen.findByText("+91•••••••210")).toBeTruthy();
    expect(screen.getAllByText(/Opted in/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Withdraw opt-in" })).toBeTruthy();
  });

  it("shows a customer's STOP as opted out, with nothing to withdraw", async () => {
    vi.spyOn(client, "listWhatsAppConsent").mockResolvedValue(
      page([event({ status: "opted_out", source: "inbound_keyword", contact_id: null }), event()]),
    );
    renderDialog();
    expect(await screen.findByText(/by replying STOP or START/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Withdraw opt-in" })).toBeNull();
  });
});

describe("recording", () => {
  it("refuses a number without a country code before asking the service", async () => {
    vi.spyOn(client, "listWhatsAppConsent").mockResolvedValue(page([]));
    const record = vi.spyOn(client, "recordWhatsAppOptIn");
    renderDialog();
    await userEvent.type(await screen.findByLabelText(/WhatsApp number/), "98765 43210");
    await userEvent.click(screen.getByRole("button", { name: "Record opt-in" }));
    expect(await screen.findByText(/with its country code/)).toBeTruthy();
    expect(record).not.toHaveBeenCalled();
  });

  it("records the number, how they said it and a default reference when the note is empty", async () => {
    vi.spyOn(client, "listWhatsAppConsent").mockResolvedValue(page([]));
    const record = vi
      .spyOn(client, "recordWhatsAppOptIn")
      .mockResolvedValue({ event: event(), effective: { allowed: true, reason: "opted_in" } });
    const { onChanged } = renderDialog();
    await userEvent.type(await screen.findByLabelText(/WhatsApp number/), "+91 98765 43210");
    await userEvent.selectOptions(screen.getByLabelText("How they told you"), "phone_call");
    await userEvent.click(screen.getByRole("button", { name: "Record opt-in" }));
    await waitFor(() =>
      expect(record).toHaveBeenCalledWith({
        contactId: CONTACT.id,
        phone: "+91 98765 43210",
        source: "phone_call",
        evidence: "Told staff on a phone call",
      }),
    );
    expect(onChanged).toHaveBeenCalledWith("WhatsApp opt-in recorded for Priya Raghavan.");
  });

  it("says when a recorded opt-in is not in effect", async () => {
    vi.spyOn(client, "listWhatsAppConsent").mockResolvedValue(page([]));
    vi.spyOn(client, "recordWhatsAppOptIn").mockResolvedValue({
      event: event(),
      effective: { allowed: false, reason: "preference_suppressed" },
    });
    renderDialog();
    await userEvent.type(await screen.findByLabelText(/WhatsApp number/), "+91 98765 43210");
    await userEvent.click(screen.getByRole("button", { name: "Record opt-in" }));
    expect(await screen.findByText("Recorded, but not in effect")).toBeTruthy();
  });

  it("withdraws without asking for the number again", async () => {
    vi.spyOn(client, "listWhatsAppConsent").mockResolvedValue(page([event()]));
    const withdraw = vi.spyOn(client, "withdrawWhatsAppOptIn").mockResolvedValue({
      event: event({ status: "opted_out" }),
      effective: { allowed: false, reason: "opted_out" },
    });
    renderDialog();
    await userEvent.click(await screen.findByRole("button", { name: "Withdraw opt-in" }));
    await userEvent.click(await screen.findByRole("button", { name: "Withdraw" }));
    await waitFor(() => expect(withdraw).toHaveBeenCalledTimes(1));
    expect(withdraw.mock.calls[0]?.[0]).toMatchObject({ contactId: CONTACT.id });
    expect(withdraw.mock.calls[0]?.[0]).not.toHaveProperty("phone");
  });
});

describe("permission gating", () => {
  it("lets a reader see the standing but not record or withdraw", async () => {
    vi.spyOn(client, "listWhatsAppConsent").mockResolvedValue(page([event()]));
    renderDialog(["engage.audience.contact.read:ORG"]);
    expect(await screen.findByText("+91•••••••210")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Record opt-in" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Withdraw opt-in" })).toBeNull();
    expect(screen.queryByLabelText(/WhatsApp number/)).toBeNull();
  });
});
