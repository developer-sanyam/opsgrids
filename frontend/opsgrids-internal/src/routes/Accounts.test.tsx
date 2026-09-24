/**
 * Accounts: every customer organization across every tenant, filterable and sortable, each one a
 * way into its own page. The behaviour worth pinning is what an operator does with it — narrow the
 * list, change its order, reach an account by mouse or by keyboard — and every state the read can
 * be in, including the sidebar search that lands here with a query.
 */

import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import { ORGANIZATIONS } from "@/api/fixtures";
import { formatMoney } from "@/lib/money";
import { findAlert, renderConsole } from "@/testing-render";

/** The live line that says how many accounts are showing (the toast layer has status regions too). */
function summary(): HTMLElement {
  const region = screen.getByText(/Combined MRR/).closest('[role="status"]');
  if (!(region instanceof HTMLElement)) throw new Error("no result summary");
  return region;
}

/** The organization names in the table, top to bottom. */
function namesInOrder(): string[] {
  const table = screen.getByRole("table");
  return within(table)
    .getAllByRole("link")
    .map((link) => link.textContent ?? "");
}

describe("Accounts — populated", () => {
  it("lists every organization with its status, revenue and a way in", async () => {
    renderConsole("/accounts");
    expect(await screen.findByRole("heading", { level: 1, name: "Accounts" })).toBeDefined();
    await screen.findByText("Northwind Metals");

    const table = screen.getByRole("table", { name: "Customer organizations across all tenants" });
    // One row per organization. (They are not `row`s to assistive technology: a clickable row is
    // an operable control, so the design system gives it the button role.)
    expect(table.querySelectorAll("tbody tr")).toHaveLength(ORGANIZATIONS.length);
    expect(screen.getByText(String(ORGANIZATIONS.length), { selector: ".tnum" })).toBeDefined();
    const combined = ORGANIZATIONS.reduce((sum, o) => sum + o.mrr_minor, 0);
    expect(summary().textContent).toContain(`Combined MRR ${formatMoney(combined, "INR")}`);

    const link = within(table).getByRole("link", { name: "Northwind Metals" });
    expect(link.getAttribute("href")).toBe("/accounts/org_a1f09c");
  });

  it("orders by revenue, richest first, until asked otherwise", async () => {
    renderConsole("/accounts");
    await screen.findByText("Northwind Metals");
    const expected = [...ORGANIZATIONS]
      .sort((a, b) => b.mrr_minor - a.mrr_minor || a.name.localeCompare(b.name))
      .map((o) => o.name);
    expect(namesInOrder()).toEqual(expected);
  });

  it("marks a locked account in words as well as an icon, and a bespoke one with its tag", async () => {
    renderConsole("/accounts");
    await screen.findByText("Vertex Mining");

    const vertex = screen.getByText("Vertex Mining").closest("tr");
    expect(vertex && within(vertex).getByText("Access locked")).toBeDefined();
    expect(vertex && within(vertex).getByText("Suspended")).toBeDefined();
    const northwind = screen.getByText("Northwind Metals").closest("tr");
    expect(northwind && within(northwind).getByText("Bespoke")).toBeDefined();
  });

  it("says a row with no revenue has none, rather than showing zero", async () => {
    renderConsole("/accounts");
    await screen.findByText("Anta Foods");
    const trial = screen.getByText("Anta Foods").closest("tr");
    expect(trial?.textContent).toContain("—");
  });
});

describe("Accounts — narrowing and ordering", () => {
  it("narrows by what is typed, and says how many are left", async () => {
    const user = userEvent.setup();
    renderConsole("/accounts");
    await screen.findByText("Northwind Metals");

    await user.type(screen.getByRole("searchbox", { name: "Search accounts" }), "north");

    expect(await screen.findByText("1", { selector: ".tnum" })).toBeDefined();
    expect(namesInOrder()).toEqual(["Northwind Metals"]);
    expect(summary().textContent).toContain("1 account");
  });

  it("narrows by lifecycle status, and shows which filter is on", async () => {
    const user = userEvent.setup();
    renderConsole("/accounts");
    await screen.findByText("Northwind Metals");

    const group = screen.getByRole("group", { name: "Status" });
    const suspended = within(group).getByRole("button", { name: "Suspended" });
    expect(suspended.getAttribute("aria-pressed")).toBe("false");
    await user.click(suspended);

    expect(suspended.getAttribute("aria-pressed")).toBe("true");
    expect(within(group).getByRole("button", { name: "All" }).getAttribute("aria-pressed")).toBe("false");
    await screen.findByText("2", { selector: ".tnum" });
    expect(namesInOrder().sort()).toEqual(["Sahara Scrap Co", "Vertex Mining"]);
  });

  it("narrows to bespoke pricing only", async () => {
    const user = userEvent.setup();
    renderConsole("/accounts");
    await screen.findByText("Northwind Metals");

    await user.click(screen.getByRole("button", { name: "Bespoke pricing only" }));

    const bespoke = ORGANIZATIONS.filter((o) => o.is_bespoke).map((o) => o.name);
    await screen.findByText(String(bespoke.length), { selector: ".tnum" });
    expect(namesInOrder().sort()).toEqual([...bespoke].sort());
  });

  it("changes the order from the sort control", async () => {
    const user = userEvent.setup();
    renderConsole("/accounts");
    await screen.findByText("Northwind Metals");

    await user.selectOptions(screen.getByLabelText("Sort"), "name_asc");

    const alphabetical = ORGANIZATIONS.map((o) => o.name).sort((a, b) => a.localeCompare(b));
    expect(namesInOrder()).toEqual(alphabetical);

    await user.selectOptions(screen.getByLabelText("Sort"), "seats_desc");
    const bySeats = [...ORGANIZATIONS].sort((a, b) => b.seats - a.seats)[0];
    expect(namesInOrder()[0]).toBe(bySeats?.name);
  });

  it("opens with the sidebar search's query already applied", async () => {
    renderConsole("/accounts?q=kaveri");
    await screen.findByText("Kaveri Steel");
    expect((screen.getByRole("searchbox", { name: "Search accounts", description: "" }) as HTMLInputElement).value).toBe("kaveri");
    expect(namesInOrder()).toEqual(["Kaveri Steel"]);
  });

  it("says no account matches, and what to change, when none does — the filters stay to be undone", async () => {
    const user = userEvent.setup();
    renderConsole("/accounts");
    await screen.findByText("Northwind Metals");

    await user.type(screen.getByRole("searchbox", { name: "Search accounts" }), "zzzz-no-such-org");

    expect(await screen.findByText("No matching accounts")).toBeDefined();
    expect(
      screen.getByText("No organization matches these filters. Try a different status, clear the bespoke filter, or adjust the search."),
    ).toBeDefined();
    expect(screen.getByRole("searchbox", { name: "Search accounts" })).toBeDefined();
  });
});

describe("Accounts — reaching an account", () => {
  it("opens the account from its name link", async () => {
    const user = userEvent.setup();
    const { router } = renderConsole("/accounts");
    await screen.findByText("Northwind Metals");

    await user.click(screen.getByRole("link", { name: "Northwind Metals" }));

    expect(router.state.location.pathname).toBe("/accounts/org_a1f09c");
    expect(await screen.findByRole("heading", { level: 1, name: "Northwind Metals" })).toBeDefined();
  });

  it("opens the account from the whole row too, by mouse and by keyboard", async () => {
    const user = userEvent.setup();
    const { router } = renderConsole("/accounts");
    await screen.findByText("Sundaram Auctioneers");

    const row = screen.getByText("Sundaram Auctioneers").closest("tr");
    if (row === null) throw new Error("no row");
    await user.click(row);
    expect(router.state.location.pathname).toBe("/accounts/org_b2e14d");

    await router.navigate("/accounts");
    await screen.findByText("Rajdhani Traders");
    const second = screen.getByText("Rajdhani Traders").closest("tr");
    if (second === null) throw new Error("no row");
    second.focus();
    await user.keyboard("{Enter}");
    expect(router.state.location.pathname).toBe("/accounts/org_c3d22e");
  });
});

describe("Accounts — every state of the read", () => {
  it("holds the layout with placeholders while it loads, and offers no table yet", async () => {
    vi.spyOn(client, "listOrganizations").mockReturnValue(new Promise(() => undefined));
    renderConsole("/accounts");

    await screen.findByRole("heading", { level: 1, name: "Accounts" });
    expect(screen.queryByRole("table")).toBeNull();
    expect(document.querySelectorAll(".animate-pulse").length).toBeGreaterThanOrEqual(8);
    // The controls are already there, so the page does not jump when the rows arrive.
    expect(screen.getByRole("searchbox", { name: "Search accounts" })).toBeDefined();
  });

  it("says the read failed, with its reference, and retries it", async () => {
    const user = userEvent.setup();
    const read = vi
      .spyOn(client, "listOrganizations")
      .mockRejectedValueOnce(new ApiError(500, "directory unavailable", { status: 500, request_id: "acc-9" }));
    renderConsole("/accounts");

    const alert = await findAlert();
    expect(alert.textContent).toContain("Couldn't load accounts");
    expect(alert.textContent).toContain("directory unavailable");
    expect(alert.textContent).toContain("Reference: acc-9");

    read.mockRestore();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Northwind Metals")).toBeDefined();
  });

  it("says a role that may not read accounts is not permitted, and does not offer to retry", async () => {
    vi.spyOn(client, "listOrganizations").mockRejectedValue(
      new ApiError(403, "This action needs one of these operator roles: billing_ops, owner, support", { status: 403 }),
    );
    renderConsole("/accounts");

    const alert = await findAlert();
    expect(alert.textContent).toContain("You can't view this");
    expect(within(alert).queryByRole("button", { name: "Try again" })).toBeNull();
  });
});
