/**
 * An account's page: who it is, what it pays, what it is entitled to, what it has used and been
 * invoiced, and its append-only credit ledger — plus the actions an operator can take on it.
 *
 * The dialogs behind those actions are exercised in `AccountActions.test.tsx`; this file is the
 * page itself, in every state it can be in and for every role that may open it. A role that may
 * read an account but not change it sees the account and no controls, and says so.
 */

import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import { organizationDetail } from "@/api/fixtures";
import { findAlert, renderConsole } from "@/testing-render";

const NORTHWIND = "org_a1f09c";
const VERTEX = "org_4b0aa6";
const BLUEOAK = "org_07c662";

describe("Account — populated", () => {
  it("names the account, its slug and region, its lifecycle state, and its bespoke pricing", async () => {
    renderConsole(`/accounts/${NORTHWIND}`);

    expect(await screen.findByRole("heading", { level: 1, name: "Northwind Metals" })).toBeDefined();
    expect(screen.getByText("northwind-metals")).toBeDefined();
    expect(screen.getByText("Mumbai, IN")).toBeDefined();
    expect(screen.getAllByText("Active").length).toBeGreaterThan(0);
    expect(screen.getByText("Bespoke pricing")).toBeDefined();
    expect(screen.getByRole("link", { name: "All accounts" }).getAttribute("href")).toBe("/accounts");
  });

  it("summarises revenue, seats, balance, tenure, activity and the person to write to", async () => {
    renderConsole(`/accounts/${NORTHWIND}`);
    await screen.findByRole("heading", { name: "Northwind Metals" });

    const summary = screen.getByText("Comp / credit balance").closest("dl");
    if (summary === null) throw new Error("no summary");
    const terms = within(summary).getAllByRole("term").map((t) => t.textContent);
    expect(terms).toEqual(["MRR", "Seats", "Comp / credit balance", "Customer since", "Last activity", "Primary contact"]);
    expect(within(summary).getByText("11 Feb 2024")).toBeDefined();
    const contact = within(summary).getByRole("link", { name: "Rukmini Shah" });
    expect(contact.getAttribute("href")).toBe("mailto:rukmini.shah@northwindmetals.in");
  });

  it("lists each subscription with the plan version it is pinned to and whether it is entitled", async () => {
    renderConsole(`/accounts/${NORTHWIND}`);
    await screen.findByRole("heading", { name: "Northwind Metals" });

    const table = screen.getByRole("table", { name: "Subscriptions for this organization" });
    const detail = organizationDetail(NORTHWIND);
    for (const sub of detail?.subscriptions ?? []) {
      expect(within(table).getByText(sub.product_name)).toBeDefined();
    }
    expect(within(table).getByText("Enterprise · Northwind · v1")).toBeDefined();
    expect(within(table).getByText("Bespoke")).toBeDefined();
    expect(within(table).getByText("Entitled")).toBeDefined();
  });

  it("shows usage with what is billed and what is included, and invoices and the ledger as tables", async () => {
    renderConsole("/accounts/org_e5b440");
    await screen.findByRole("heading", { name: "Deccan Commodities" });

    expect(screen.getByText("Payslips generated")).toBeDefined();
    expect(screen.getByText("Billable")).toBeDefined();
    expect(screen.getByText(/of 2,000 included/)).toBeDefined();
    expect(screen.getByRole("table", { name: "Invoices" })).toBeDefined();
    expect(screen.getByRole("table", { name: "Credit ledger" })).toBeDefined();
    expect(screen.getByText("Balance now")).toBeDefined();
  });

  it("says a suspended account is locked out, and offers to reactivate it rather than suspend it", async () => {
    renderConsole(`/accounts/${VERTEX}`);
    await screen.findByRole("heading", { name: "Vertex Mining" });

    expect(screen.getByText("Access locked")).toBeDefined();
    expect(screen.getByRole("button", { name: "Reactivate" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "Suspend" })).toBeNull();
    expect(screen.getByText("Blocked")).toBeDefined();
  });

  it("says when a trial ends", async () => {
    renderConsole(`/accounts/${BLUEOAK}`);
    await screen.findByRole("heading", { name: "BlueOak Retail" });
    expect(screen.getByText("Trial ends in 12 days")).toBeDefined();
  });

  it("says an account with nothing yet has nothing — no usage, no invoices, no grants", async () => {
    const empty = organizationDetail(NORTHWIND);
    if (empty === null) throw new Error("no fixture");
    vi.spyOn(client, "getOrganization").mockResolvedValue({ ...empty, usage: [], invoices: [], ledger: [] });
    renderConsole(`/accounts/${NORTHWIND}`);
    await screen.findByRole("heading", { name: "Northwind Metals" });

    expect(screen.getByText("No metered usage recorded for this account.")).toBeDefined();
    expect(screen.getByText("No invoices yet — this account has never been billed.")).toBeDefined();
    expect(screen.getByText("No credit or comp grants on this account.")).toBeDefined();
  });
});

describe("Account — the actions offered", () => {
  it("offers every action to a role that may change accounts", async () => {
    renderConsole(`/accounts/${NORTHWIND}`, ["owner"]);
    await screen.findByRole("heading", { name: "Northwind Metals" });

    expect(screen.getByRole("button", { name: "Suspend" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Grant credit" })).toBeDefined();
    await waitFor(() => expect(screen.getByRole("button", { name: "Bespoke price" }).hasAttribute("disabled")).toBe(false));
    expect(screen.getByRole("button", { name: "Migrate plan" }).hasAttribute("disabled")).toBe(false);
    expect(screen.queryByText(/Read-only/)).toBeNull();
  });

  it("offers a role that may only read the account no controls, and says why", async () => {
    renderConsole(`/accounts/${NORTHWIND}`, ["support"]);
    await screen.findByRole("heading", { name: "Northwind Metals" });

    for (const name of ["Suspend", "Reactivate", "Grant credit", "Bespoke price", "Migrate plan"]) {
      expect(screen.queryByRole("button", { name }), name).toBeNull();
    }
    expect(screen.getByText("Read-only — your operator role doesn't include account changes.")).toBeDefined();
    // Reading is intact.
    expect(screen.getByRole("table", { name: "Subscriptions for this organization" })).toBeDefined();
  });

  it("holds the pricing actions back until the catalog they need has loaded", async () => {
    vi.spyOn(client, "getCatalog").mockReturnValue(new Promise(() => undefined));
    renderConsole(`/accounts/${NORTHWIND}`);
    await screen.findByRole("heading", { name: "Northwind Metals" });

    expect(screen.getByRole("button", { name: "Bespoke price" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Migrate plan" }).hasAttribute("disabled")).toBe(true);
    // The actions that need no catalog are not held hostage to it.
    expect(screen.getByRole("button", { name: "Grant credit" }).hasAttribute("disabled")).toBe(false);
  });

  it("says why the pricing actions are unavailable when the catalog cannot be loaded, and retries it", async () => {
    const user = userEvent.setup();
    const catalog = vi
      .spyOn(client, "getCatalog")
      .mockRejectedValueOnce(new ApiError(404, "Not found", { status: 404, detail: "Not found", request_id: "cat-3" }));
    renderConsole(`/accounts/${NORTHWIND}`);
    await screen.findByRole("heading", { name: "Northwind Metals" });

    const notice = await screen.findByText(
      "Bespoke price and Migrate plan are unavailable — the plan catalog couldn't be loaded.",
    );
    expect(notice.closest('[role="status"]')?.textContent).toContain("Reference: cat-3");
    expect(screen.getByRole("button", { name: "Bespoke price" }).hasAttribute("disabled")).toBe(true);

    catalog.mockRestore();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Bespoke price" }).hasAttribute("disabled")).toBe(false));
  });
});

describe("Account — every state of the read", () => {
  it("holds the layout with placeholders while it loads", async () => {
    vi.spyOn(client, "getOrganization").mockReturnValue(new Promise(() => undefined));
    renderConsole(`/accounts/${NORTHWIND}`);

    await waitFor(() => expect(document.querySelectorAll(".animate-pulse").length).toBeGreaterThanOrEqual(3));
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });

  it("says an account that does not exist does not, without offering a retry that could not help", async () => {
    renderConsole("/accounts/org_nope");

    const alert = await findAlert();
    expect(alert.textContent).toContain("Not found");
    expect(alert.textContent).toContain("No such organization");
    expect(within(alert).queryByRole("button", { name: "Try again" })).toBeNull();
    expect(screen.getByRole("link", { name: "All accounts" }).getAttribute("href")).toBe("/accounts");
  });

  it("says a real failure is one, with its reference, and retries", async () => {
    const user = userEvent.setup();
    const read = vi
      .spyOn(client, "getOrganization")
      .mockRejectedValueOnce(new ApiError(503, "account-service could not be reached in time", { status: 503, request_id: "det-7" }));
    renderConsole(`/accounts/${NORTHWIND}`);

    const alert = await findAlert();
    expect(alert.textContent).toContain("Couldn't load this account");
    expect(alert.textContent).toContain("account-service could not be reached in time");
    expect(alert.textContent).toContain("Reference: det-7");

    read.mockRestore();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Northwind Metals" })).toBeDefined();
  });

  it("says a role that may not read an account is not permitted, in the server's words", async () => {
    vi.spyOn(client, "getOrganization").mockRejectedValue(
      new ApiError(403, "This action needs one of these operator roles: billing_ops, owner, support", { status: 403 }),
    );
    renderConsole(`/accounts/${NORTHWIND}`);

    const alert = await findAlert();
    expect(alert.textContent).toContain("You can't view this");
    expect(within(alert).queryByRole("button", { name: "Try again" })).toBeNull();
  });
});
