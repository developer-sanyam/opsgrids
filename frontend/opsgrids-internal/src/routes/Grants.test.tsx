/**
 * Grants & Credits: the credit ledger across every tenant, and who is on complimentary access and
 * when it lapses — a query over append-only entries. Two writes come from here, and both are new
 * rows: a grant, and a reversal. A reversal never edits or deletes the grant it takes back.
 */

import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import { CREDIT_LEDGER } from "@/api/fixtures";
import { renderConsole } from "@/testing-render";

function ledgerRows(): HTMLTableRowElement[] {
  const table = screen.getByRole("table", { name: "Credit-ledger entries across all tenants" });
  return [...table.querySelectorAll<HTMLTableRowElement>("tbody tr")];
}

/** The card listing who is on comp access now. */
function onComp(): HTMLElement {
  const card = screen.getByRole("heading", { level: 2, name: "On comp access now" }).closest(".rounded-card");
  if (!(card instanceof HTMLElement)) throw new Error("no comp card");
  return card;
}

describe("Grants — populated", () => {
  it("lists every ledger entry with its organization, kind, reason, expiry and signed amount", async () => {
    renderConsole("/grants");
    expect(await screen.findByRole("heading", { level: 1, name: "Grants & Credits" })).toBeDefined();
    await screen.findByRole("table", { name: "Credit-ledger entries across all tenants" });

    expect(ledgerRows()).toHaveLength(CREDIT_LEDGER.length);
    const text = screen.getByRole("table").textContent ?? "";
    expect(text).toContain("Lotus Manufacturing");
    expect(text).toContain("Grant");
    expect(text).toContain("Comp");
  });

  it("says who is on comp access now, worth what, and how many lapse within a month", async () => {
    renderConsole("/grants");
    await screen.findByRole("table", { name: "Credit-ledger entries across all tenants" });

    // Two complimentary grants cover today; the third fixture's window ended in 2025.
    const card = onComp();
    expect(within(card).getByText("Lotus Manufacturing")).toBeDefined();
    expect(within(card).getByText("BlueOak Retail")).toBeDefined();
    expect(within(card).queryByText("Rajdhani Traders")).toBeNull();
    expect(card.textContent).toContain("Lapses in 13 days");
    expect(card.textContent).toContain("Active value");
    // BlueOak's window closes in under two weeks; Lotus's in over two months.
    expect(within(card).getByText("Lapsing ≤ 30d").parentElement?.textContent).toContain("1");
  });

  it("offers to reverse only a grant that is still in force", async () => {
    renderConsole("/grants");
    await screen.findByRole("table", { name: "Credit-ledger entries across all tenants" });

    const reversible = screen.getAllByRole("button", { name: /^Reverse grant to / });
    const names = reversible.map((b) => b.getAttribute("aria-label"));
    expect(names).toContain("Reverse grant to Lotus Manufacturing");
    expect(names).not.toContain("Reverse grant to Rajdhani Traders");
  });

  it("links each organization to its page", async () => {
    renderConsole("/grants");
    await screen.findByRole("table", { name: "Credit-ledger entries across all tenants" });
    expect(within(onComp()).getByRole("link", { name: /Lotus Manufacturing/ }).getAttribute("href")).toBe("/accounts/org_7e3dd9");
  });
});

describe("Grants — narrowing", () => {
  it("narrows by what is typed", async () => {
    const user = userEvent.setup();
    renderConsole("/grants");
    await screen.findByRole("table", { name: "Credit-ledger entries across all tenants" });

    await user.type(screen.getByRole("searchbox", { name: "Search the ledger" }), "lotus");

    const expected = CREDIT_LEDGER.filter((e) => e.organization_name === "Lotus Manufacturing" || e.reason.toLowerCase().includes("lotus")).length;
    await waitFor(() => expect(ledgerRows()).toHaveLength(expected));
  });

  it("narrows to comp entries, and shows the filter is on", async () => {
    const user = userEvent.setup();
    renderConsole("/grants");
    await screen.findByRole("table", { name: "Credit-ledger entries across all tenants" });

    const pill = screen.getByRole("button", { name: "Comp entries only" });
    expect(pill.getAttribute("aria-pressed")).toBe("false");
    await user.click(pill);

    expect(pill.getAttribute("aria-pressed")).toBe("true");
    await waitFor(() => expect(ledgerRows()).toHaveLength(CREDIT_LEDGER.filter((e) => e.is_comp).length));
  });

  it("says nothing matches, and why, when nothing does", async () => {
    const user = userEvent.setup();
    renderConsole("/grants");
    await screen.findByRole("table", { name: "Credit-ledger entries across all tenants" });

    await user.type(screen.getByRole("searchbox", { name: "Search the ledger" }), "zzzz-nothing");

    expect(await screen.findByText("No ledger entries")).toBeDefined();
    expect(screen.getByText("No grant, consumption or reversal matches these filters.")).toBeDefined();
  });
});

describe("Grants — recording a grant", () => {
  async function openGrant() {
    const user = userEvent.setup();
    const utils = renderConsole("/grants");
    await screen.findByRole("table", { name: "Credit-ledger entries across all tenants" });
    const opener = screen.getByRole("button", { name: "Grant credit" });
    await waitFor(() => expect(opener.hasAttribute("disabled")).toBe(false));
    await user.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "Grant credit or comp access" });
    return { user, dialog, ...utils };
  }

  it("asks which customer, the kind of grant, the amount, an optional expiry and the reason", async () => {
    const { dialog } = await openGrant();
    expect(document.activeElement).toBe(within(dialog).getByLabelText("Customer"));
    expect(within(dialog).getByRole("group", { name: "Grant type" })).toBeDefined();
    expect(within(dialog).getByLabelText("Amount (₹)")).toBeDefined();
    expect(within(dialog).getByLabelText(/Expires/)).toBeDefined();
    expect(within(dialog).getByLabelText("Reason (required)")).toBeDefined();
  });

  it("says what is missing on its own field, and sends nothing", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user, dialog } = await openGrant();

    await user.click(within(dialog).getByRole("button", { name: "Record grant" }));

    expect(within(dialog).getByText("Enter an amount in rupees, like 45000.")).toBeDefined();
    expect(within(dialog).getByText("Give a reason — it goes into the audit log.")).toBeDefined();
    expect(post).not.toHaveBeenCalled();
  });

  it("records it for the customer chosen, then says so and re-reads the ledger", async () => {
    const post = vi.spyOn(client, "postOverride");
    const ledger = vi.spyOn(client, "listLedger");
    const { user, dialog } = await openGrant();

    const customer = within(dialog).getByLabelText("Customer") as HTMLSelectElement;
    const chosen = [...customer.options].find((o) => o.textContent === "Deccan Commodities");
    if (chosen === undefined) throw new Error("Deccan Commodities not offered");
    await user.selectOptions(customer, chosen.value);
    await user.type(within(dialog).getByLabelText("Amount (₹)"), "45000");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Goodwill");
    await user.click(within(dialog).getByRole("button", { name: "Record grant" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0]?.[0]).toMatchObject({
      kind: "comp_grant",
      organization_id: "org_e5b440",
      amount_minor: 4_500_000,
      is_comp: true,
      valid_until: null,
    });
    expect(await screen.findByText("Grant recorded.")).toBeDefined();
    await waitFor(() => expect(ledger).toHaveBeenCalledTimes(2));
  });

  it("keeps the dialog on a refusal, with the server's sentence and reference", async () => {
    vi.spyOn(client, "postOverride").mockRejectedValueOnce(
      new ApiError(503, "billing-service is unavailable", { status: 503, detail: "billing-service is unavailable", request_id: "grant-5" }),
    );
    const { user, dialog } = await openGrant();

    await user.type(within(dialog).getByLabelText("Amount (₹)"), "45000");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Goodwill");
    await user.click(within(dialog).getByRole("button", { name: "Record grant" }));

    expect(await within(dialog).findByText("billing-service is unavailable")).toBeDefined();
    expect(within(dialog).getByText("Reference: grant-5")).toBeDefined();
  });
});

describe("Grants — reversing a grant", () => {
  async function openReverse(name: string) {
    const user = userEvent.setup();
    const utils = renderConsole("/grants");
    await screen.findByRole("table", { name: "Credit-ledger entries across all tenants" });
    await user.click(screen.getByRole("button", { name: `Reverse grant to ${name}` }));
    const dialog = await screen.findByRole("dialog", { name: "Reverse grant" });
    return { user, dialog, ...utils };
  }

  it("says what will be reversed, that the original stays, and focuses the reason", async () => {
    const { dialog } = await openReverse("Lotus Manufacturing");

    expect(within(dialog).getByText("Writes a reversing entry. The original grant stays on the ledger, unchanged.")).toBeDefined();
    expect(dialog.textContent).toContain("Reversing");
    expect(dialog.textContent).toContain("Lotus Manufacturing");
    expect(dialog.textContent).toContain("Migration goodwill — 3 months complimentary Scale");
    expect(document.activeElement).toBe(within(dialog).getByLabelText("Reason (required)"));
    expect(within(dialog).getByRole("button", { name: "Reverse grant" }).className).toContain("bg-negative");
  });

  it("says a missing reason is missing, and sends nothing", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user, dialog } = await openReverse("Lotus Manufacturing");

    await user.click(within(dialog).getByRole("button", { name: "Reverse grant" }));

    expect(within(dialog).getByText("Give a reason — it goes into the audit log.")).toBeDefined();
    expect(post).not.toHaveBeenCalled();
  });

  it("writes an opposite entry that names what it reverses, and leaves the original alone", async () => {
    const post = vi.spyOn(client, "postOverride");
    const ledger = vi.spyOn(client, "listLedger");
    const { user, dialog } = await openReverse("Lotus Manufacturing");

    await user.type(within(dialog).getByLabelText("Reason (required)"), "Payment recovered");
    await user.click(within(dialog).getByRole("button", { name: "Reverse grant" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const original = CREDIT_LEDGER.find((e) => e.entry_id === "le_lotus_comp_01");
    expect(post.mock.calls[0]?.[0]).toMatchObject({
      kind: "comp_grant",
      organization_id: "org_7e3dd9",
      amount_minor: -(original?.amount_minor ?? 0),
      is_comp: true,
      reason: "Reversal of le_lotus_comp_01 — Payment recovered",
      valid_until: null,
    });
    expect(await screen.findByText("Grant reversed.")).toBeDefined();
    await waitFor(() => expect(ledger).toHaveBeenCalledTimes(2));
  });
});

describe("Grants — every state of the read", () => {
  it("holds the layout with placeholders while it loads, and offers no table yet", async () => {
    vi.spyOn(client, "listLedger").mockReturnValue(new Promise(() => undefined));
    renderConsole("/grants");
    await screen.findByRole("heading", { level: 1, name: "Grants & Credits" });
    expect(screen.queryByRole("table")).toBeNull();
    expect(document.querySelectorAll(".animate-pulse").length).toBeGreaterThanOrEqual(6);
  });

  it("says nobody is on comp access when nobody is", async () => {
    vi.spyOn(client, "listLedger").mockResolvedValue({ items: [], total: 0 });
    renderConsole("/grants");
    expect(await screen.findByText("No account is on comp access right now.")).toBeDefined();
    expect(await screen.findByText("No ledger entries")).toBeDefined();
  });

  it("says the read failed, once per place it fails, and retries it", async () => {
    const user = userEvent.setup();
    const read = vi
      .spyOn(client, "listLedger")
      .mockRejectedValueOnce(new ApiError(500, "billing-service is down", { status: 500, request_id: "led-1" }));
    renderConsole("/grants");

    await waitFor(() => expect(screen.getAllByText("Couldn't load the credit ledger").length).toBe(2));
    expect(screen.getAllByText("Reference: led-1").length).toBe(2);

    read.mockRestore();
    await user.click(screen.getAllByRole("button", { name: "Try again" })[0] as HTMLElement);
    await screen.findByRole("table", { name: "Credit-ledger entries across all tenants" });
  });

  it("says a 403 is a role, in the server's words, and offers no retry", async () => {
    vi.spyOn(client, "listLedger").mockRejectedValue(
      new ApiError(403, "This action needs one of these operator roles: billing_ops, owner", { status: 403 }),
    );
    renderConsole("/grants");
    const alerts = await screen.findAllByText("You can't view this");
    expect(alerts.length).toBe(2);
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});

describe("Grants — for a role that may not open it", () => {
  it("says so, names who may, and reads nothing", async () => {
    const read = vi.spyOn(client, "listLedger");
    renderConsole("/grants", ["support"]);
    expect(await screen.findByText("You can't open grants and credits")).toBeDefined();
    expect(screen.getByText(/It is open to: Owner, Billing ops\./)).toBeDefined();
    expect(read).not.toHaveBeenCalled();
  });
});
