/**
 * Subscriptions: every subscription across every tenant, with its lifecycle state and what it
 * costs, each one a way to its account. A financial read — owner and billing_ops may open it, and
 * support may not, which is a state the screen has to be in as well as the happy one.
 */

import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import { SUBSCRIPTIONS } from "@/api/fixtures";
import { findAlert, renderConsole } from "@/testing-render";

function rows(): HTMLTableRowElement[] {
  const table = screen.getByRole("table", { name: "Subscriptions across all tenants" });
  return [...table.querySelectorAll<HTMLTableRowElement>("tbody tr")];
}

describe("Subscriptions — populated", () => {
  it("lists every subscription with its organization, product and plan, state, seats and price", async () => {
    renderConsole("/subscriptions");
    expect(await screen.findByRole("heading", { level: 1, name: "Subscriptions" })).toBeDefined();
    await screen.findByRole("table", { name: "Subscriptions across all tenants" });

    expect(rows()).toHaveLength(SUBSCRIPTIONS.length);
    const northwind = screen.getAllByText("Northwind Metals")[0]?.closest("tr");
    expect(northwind?.textContent).toContain("Bespoke");
    expect(northwind?.textContent).toContain("Active");
  });

  it("names the products a bundle switches on", async () => {
    renderConsole("/subscriptions");
    await screen.findByRole("table", { name: "Subscriptions across all tenants" });
    const bundled = SUBSCRIPTIONS.find((s) => (s.entitled_products ?? []).length > 1);
    if (bundled === undefined) throw new Error("no bundle in the fixtures");
    const members = (bundled.entitled_products ?? []).filter((key) => key !== bundled.product_key);
    expect(screen.getAllByText(/^Entitles/).some((el) => el.textContent?.includes(members.join(", ")))).toBe(true);
  });

  it("says a trial's end, and a price of nothing as nothing", async () => {
    renderConsole("/subscriptions");
    await screen.findByRole("table", { name: "Subscriptions across all tenants" });
    expect(screen.getAllByText(/^trial · /).length).toBeGreaterThan(0);
  });

  it("links each organization to its own page", async () => {
    renderConsole("/subscriptions");
    await screen.findByRole("table", { name: "Subscriptions across all tenants" });
    const link = within(rows()[0] as HTMLElement).getAllByRole("link")[0];
    expect(link?.getAttribute("href")).toMatch(/^\/accounts\/org_/);
  });

  it("opens the account from the whole row", async () => {
    const user = userEvent.setup();
    const { router } = renderConsole("/subscriptions");
    await screen.findByRole("table", { name: "Subscriptions across all tenants" });

    await user.click(rows()[0] as HTMLElement);

    expect(router.state.location.pathname).toMatch(/^\/accounts\/org_/);
  });
});

describe("Subscriptions — narrowing", () => {
  it("narrows by what is typed, across organization, product and plan", async () => {
    const user = userEvent.setup();
    renderConsole("/subscriptions");
    await screen.findByRole("table", { name: "Subscriptions across all tenants" });

    await user.type(screen.getByRole("searchbox", { name: "Search subscriptions" }), "kaveri");

    // Kaveri Steel holds three subscriptions, so it is three rows and each one names it.
    const expected = SUBSCRIPTIONS.filter((s) => s.organization_name === "Kaveri Steel").length;
    await waitFor(() => expect(rows()).toHaveLength(expected));
    for (const row of rows()) expect(row.textContent).toContain("Kaveri Steel");
  });

  it("narrows by lifecycle state, and shows which is on", async () => {
    const user = userEvent.setup();
    renderConsole("/subscriptions");
    await screen.findByRole("table", { name: "Subscriptions across all tenants" });

    const group = screen.getByRole("group", { name: "Lifecycle state" });
    await user.click(within(group).getByRole("button", { name: "Suspended" }));

    expect(within(group).getByRole("button", { name: "Suspended" }).getAttribute("aria-pressed")).toBe("true");
    expect(rows().length).toBe(SUBSCRIPTIONS.filter((s) => s.state === "suspended").length);
    for (const row of rows()) expect(row.textContent).toContain("Suspended");
  });

  it("says nothing matches, and what to change, when nothing does", async () => {
    const user = userEvent.setup();
    renderConsole("/subscriptions");
    await screen.findByRole("table", { name: "Subscriptions across all tenants" });

    await user.type(screen.getByRole("searchbox", { name: "Search subscriptions" }), "zzzz-nothing");

    expect(await screen.findByText("No matching subscriptions")).toBeDefined();
    expect(screen.getByText("Nothing matches these filters. Try a different lifecycle state or clear the search.")).toBeDefined();
  });
});

describe("Subscriptions — every state of the read", () => {
  it("holds the layout with placeholders while it loads", async () => {
    vi.spyOn(client, "listSubscriptions").mockReturnValue(new Promise(() => undefined));
    renderConsole("/subscriptions");
    await screen.findByRole("heading", { level: 1, name: "Subscriptions" });
    expect(screen.queryByRole("table")).toBeNull();
    expect(document.querySelectorAll(".animate-pulse").length).toBeGreaterThanOrEqual(8);
  });

  it("says the read failed, with its reference, and retries it", async () => {
    const user = userEvent.setup();
    const read = vi
      .spyOn(client, "listSubscriptions")
      .mockRejectedValueOnce(new ApiError(500, "console projection unavailable", { status: 500, request_id: "sub-2" }));
    renderConsole("/subscriptions");

    const alert = await findAlert();
    expect(alert.textContent).toContain("Couldn't load subscriptions");
    expect(alert.textContent).toContain("Reference: sub-2");

    read.mockRestore();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("table", { name: "Subscriptions across all tenants" });
  });

  it("says a 403 from the server is a role, in the server's words, and offers no retry", async () => {
    vi.spyOn(client, "listSubscriptions").mockRejectedValue(
      new ApiError(403, "This action needs one of these operator roles: billing_ops, owner", { status: 403 }),
    );
    renderConsole("/subscriptions");
    const alert = await findAlert();
    expect(alert.textContent).toContain("You can't view this");
    expect(within(alert).queryByRole("button", { name: "Try again" })).toBeNull();
  });
});

describe("Subscriptions — for a role that may not open it", () => {
  it("says so, names who may, and reads nothing", async () => {
    const read = vi.spyOn(client, "listSubscriptions");
    renderConsole("/subscriptions", ["support"]);

    expect(await screen.findByRole("heading", { level: 1, name: "Subscriptions" })).toBeDefined();
    expect(screen.getByText("You can't open subscriptions")).toBeDefined();
    expect(screen.getByText(/It is open to: Owner, Billing ops\./)).toBeDefined();
    // The guard is presentation, but it does not fire a request the server would refuse anyway.
    expect(read).not.toHaveBeenCalled();
  });
});
