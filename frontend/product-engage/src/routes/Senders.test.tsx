import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { DomainOut, Page, SenderOut } from "@/api/types";
import { SendersPage } from "./Senders";

/**
 * The gate, from the screen's side.
 *
 * A sender is usable because a test message reached the provider, and nothing else. What is
 * pinned here is that the screen never says otherwise: an untested sender reads as unusable in
 * words, the flip happens on the service's answer rather than on the click that asked for it, and
 * a failed test leaves the sender exactly as unusable as it was.
 */

const VERIFIED_DOMAIN: DomainOut = {
  id: "3f6a1c20-7d84-4e1b-9c53-0a2e8b74d611",
  domain: "vaayu-logistics.example.com",
  status: "verified",
  connection_kind: "google",
  dkim_selector: "google",
  spf_include: null,
  records: [],
  last_checked_at: "2026-09-19T06:12:44Z",
  created_at: "2026-08-28T11:04:09Z",
};

const PENDING_DOMAIN: DomainOut = {
  id: "b90d5e47-2c31-4a88-8f70-61d4c9a5e223",
  domain: "vaayu-freight.example.com",
  status: "pending",
  connection_kind: "google",
  dkim_selector: "google",
  spf_include: null,
  records: [],
  last_checked_at: null,
  created_at: "2026-09-20T15:38:57Z",
};

const TESTED: SenderOut = {
  id: "5c8e2a19-4b6f-4d02-a37e-9f15c0b8d744",
  domain_id: VERIFIED_DOMAIN.id,
  name: "Vaayu Logistics Support",
  address: "support@vaayu-logistics.example.com",
  reply_to: "support@vaayu-logistics.example.com",
  signature: "",
  department: "Support",
  usable: true,
  verified_at: "2026-08-29T09:22:31Z",
  created_at: "2026-08-29T09:18:02Z",
};

const UNTESTED: SenderOut = {
  id: "1a47f0d6-88b3-4c95-b214-6e7d3f92ac58",
  domain_id: VERIFIED_DOMAIN.id,
  name: "Priya Raghavan",
  address: "priya.raghavan@vaayu-logistics.example.com",
  reply_to: "",
  signature: "",
  department: "Key accounts",
  usable: false,
  verified_at: null,
  created_at: "2026-09-18T13:47:20Z",
};

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

/** Render the screen against a given pair of lists and wait for the load to settle. */
async function mount(senders: SenderOut[], domains: DomainOut[]) {
  vi.spyOn(client, "listSenders").mockResolvedValue(page(senders));
  vi.spyOn(client, "listDomains").mockResolvedValue(page(domains));
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <SendersPage />
    </MemoryRouter>,
  );
  if (senders.length > 0) {
    await screen.findByRole("table");
  } else {
    await screen.findByText("No senders yet");
  }
  return user;
}

/** The row whose row header names this sender. */
function rowFor(name: string): HTMLElement {
  const row = screen.getByRole("rowheader", { name: new RegExp(name) }).closest("tr");
  if (row === null) throw new Error(`No row for ${name}`);
  return row;
}

describe("the usable / unusable state", () => {
  it("says in words that an untested sender cannot send, not only in a pill", async () => {
    await mount([UNTESTED], [VERIFIED_DOMAIN]);

    const row = rowFor("Priya Raghavan");
    expect(within(row).getByText("Not tested yet")).toBeTruthy();
    expect(
      within(row).getByText(
        "No campaign can send as this address until a test message is delivered.",
      ),
    ).toBeTruthy();
    expect(within(row).getByRole("button", { name: /Send test/ })).toBeTruthy();
  });

  it("shows a tested sender as delivered, with when it happened", async () => {
    await mount([TESTED], [VERIFIED_DOMAIN]);

    const row = rowFor("Vaayu Logistics Support");
    expect(within(row).getByText("Test message delivered")).toBeTruthy();
    expect(within(row).getByText(/^Delivered /)).toBeTruthy();
    // Already proven, so the control offers to repeat the test rather than to open the gate.
    expect(within(row).getByRole("button", { name: /Test again/ })).toBeTruthy();
  });

  it("keeps the two apart in one list", async () => {
    await mount([TESTED, UNTESTED], [VERIFIED_DOMAIN]);

    expect(within(rowFor("Vaayu Logistics Support")).getByText("Test message delivered")).toBeTruthy();
    expect(within(rowFor("Priya Raghavan")).getByText("Not tested yet")).toBeTruthy();
  });
});

describe("sending the test message", () => {
  it("flips the sender on the service's answer, not on the click", async () => {
    const user = await mount([UNTESTED], [VERIFIED_DOMAIN]);
    const spy = vi.spyOn(client, "sendTestMessage").mockResolvedValue({
      ...UNTESTED,
      usable: true,
      verified_at: "2026-09-21T10:15:00Z",
    });

    await user.click(within(rowFor("Priya Raghavan")).getByRole("button", { name: /Send test/ }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Send it to"), "priya@vaayu.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Send test" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(spy).toHaveBeenCalledWith(UNTESTED.id, { to_address: "priya@vaayu.example.com" });
    expect(within(rowFor("Priya Raghavan")).getByText("Test message delivered")).toBeTruthy();
  });

  it("leaves the sender unusable when the provider refuses, and quotes the provider", async () => {
    const user = await mount([UNTESTED], [VERIFIED_DOMAIN]);
    vi.spyOn(client, "sendTestMessage").mockRejectedValue(
      new ApiError(502, "535 5.7.8 Username and Password not accepted", null),
    );

    await user.click(within(rowFor("Priya Raghavan")).getByRole("button", { name: /Send test/ }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Send it to"), "priya@vaayu.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Send test" }));

    expect(await screen.findByText("535 5.7.8 Username and Password not accepted")).toBeTruthy();
    // The recipient stays put — the next step is usually to fix the connection and try again.
    expect((within(dialog).getByLabelText("Send it to") as HTMLInputElement).value).toBe(
      "priya@vaayu.example.com",
    );
    expect(within(rowFor("Priya Raghavan")).getByText("Not tested yet")).toBeTruthy();
  });
});

describe("adding a sender", () => {
  it("offers only verified domains, because the writer refuses the rest", async () => {
    const user = await mount([TESTED], [VERIFIED_DOMAIN, PENDING_DOMAIN]);

    await user.click(screen.getByRole("button", { name: "Add a sender" }));
    const dialog = screen.getByRole("dialog");
    const options = within(dialog).getAllByRole("option");

    expect(options.map((option) => option.textContent)).toEqual([
      "Choose a verified domain",
      "vaayu-logistics.example.com",
    ]);
  });

  it("points at Domains instead of offering a form when nothing is verified yet", async () => {
    await mount([], [PENDING_DOMAIN]);

    expect(screen.getByText("No verified domain to send from yet")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add a sender" })).toBeNull();
    expect(screen.getByRole("link", { name: "Open Domains" })).toBeTruthy();
  });
});
