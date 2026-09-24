/**
 * Audit log: every operator override and cross-tenant sensitive read, recorded by the owning
 * service. It exists so that an operator looking at a customer is itself something that can be
 * looked at — which is why it is a read for owner and support and *not* for billing_ops, and why
 * the screen has to be honest about a failed action as well as a successful one.
 */

import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import { AUDIT_ACTIONS, AUDIT_LOG } from "@/api/fixtures";
import { findAlert, renderConsole } from "@/testing-render";

function rows(): HTMLTableRowElement[] {
  const table = screen.getByRole("table", { name: "Operator audit log" });
  return [...table.querySelectorAll<HTMLTableRowElement>("tbody tr")];
}

describe("Audit — populated", () => {
  it("lists every entry with when, who, what, where, why and how it ended", async () => {
    renderConsole("/audit");
    expect(await screen.findByRole("heading", { level: 1, name: "Audit log" })).toBeDefined();
    await screen.findByRole("table", { name: "Operator audit log" });

    expect(rows()).toHaveLength(AUDIT_LOG.length);
    const first = rows()[0];
    expect(first?.textContent).toContain("directory.read.sensitive");
    expect(first?.textContent).toContain("Priya Venkatesan");
    expect(first?.textContent).toContain("priya.venkatesan@opsgrids.com");
    expect(first?.textContent).toContain("Unmasked billing contact for support case #4821");
    expect(first?.textContent).toContain("Success");
  });

  it("says a failed action failed, in words as well as colour", async () => {
    renderConsole("/audit");
    await screen.findByRole("table", { name: "Operator audit log" });
    const failed = AUDIT_LOG.filter((a) => a.result === "failed");
    expect(failed.length).toBeGreaterThan(0);
    expect(screen.getAllByText("Failed")).toHaveLength(failed.length);
  });

  it("links an entry to the organization it touched, and says platform for one that touched none", async () => {
    renderConsole("/audit");
    await screen.findByRole("table", { name: "Operator audit log" });
    expect(within(rows()[0] as HTMLElement).getByRole("link", { name: "Deccan Commodities" }).getAttribute("href")).toBe(
      "/accounts/org_e5b440",
    );
    if (AUDIT_LOG.some((a) => a.organization_id === null)) expect(screen.getAllByText("platform").length).toBeGreaterThan(0);
  });
});

describe("Audit — narrowing", () => {
  it("narrows by what is typed, across operator, organization and summary", async () => {
    const user = userEvent.setup();
    renderConsole("/audit");
    await screen.findByRole("table", { name: "Operator audit log" });

    await user.type(screen.getByRole("searchbox", { name: "Search the audit log" }), "rahul");

    const expected = AUDIT_LOG.filter((a) => a.actor.toLowerCase().includes("rahul") || a.summary.toLowerCase().includes("rahul")).length;
    await waitFor(() => expect(rows()).toHaveLength(expected));
    for (const row of rows()) expect(row.textContent).toContain("Rahul Menon");
  });

  it("offers one filter per action that occurs, and narrows to it", async () => {
    const user = userEvent.setup();
    renderConsole("/audit");
    await screen.findByRole("table", { name: "Operator audit log" });

    const group = screen.getByRole("group", { name: "Action" });
    const offered = within(group).getAllByRole("button").map((b) => b.textContent);
    expect(offered).toEqual(["All actions", ...AUDIT_ACTIONS]);

    const suspend = within(group).getByRole("button", { name: "override.suspend" });
    await user.click(suspend);
    expect(suspend.getAttribute("aria-pressed")).toBe("true");
    await waitFor(() => expect(rows()).toHaveLength(AUDIT_LOG.filter((a) => a.action === "override.suspend").length));
  });

  it("says nothing matches, and what to change, when nothing does", async () => {
    const user = userEvent.setup();
    renderConsole("/audit");
    await screen.findByRole("table", { name: "Operator audit log" });

    await user.type(screen.getByRole("searchbox", { name: "Search the audit log" }), "zzzz-nothing");

    expect(await screen.findByText("No matching audit entries")).toBeDefined();
    expect(screen.getByText("Nothing matches these filters. Try a different action or clear the search.")).toBeDefined();
  });
});

describe("Audit — every state of the read", () => {
  it("holds the layout with placeholders while it loads", async () => {
    vi.spyOn(client, "listAudit").mockReturnValue(new Promise(() => undefined));
    renderConsole("/audit");
    await screen.findByRole("heading", { level: 1, name: "Audit log" });
    expect(screen.queryByRole("table")).toBeNull();
    expect(document.querySelectorAll(".animate-pulse").length).toBeGreaterThanOrEqual(8);
  });

  it("says an empty log is empty, with the table's own message", async () => {
    vi.spyOn(client, "listAudit").mockResolvedValue({ items: [], total: 0 });
    renderConsole("/audit");
    expect(await screen.findByText("No matching audit entries")).toBeDefined();
  });

  it("says the read failed, with its reference, and retries it", async () => {
    const user = userEvent.setup();
    const read = vi
      .spyOn(client, "listAudit")
      .mockRejectedValueOnce(new ApiError(500, "console audit store unavailable", { status: 500, request_id: "aud-8" }));
    renderConsole("/audit");

    const alert = await findAlert();
    expect(alert.textContent).toContain("Couldn't load the audit log");
    expect(alert.textContent).toContain("Reference: aud-8");

    read.mockRestore();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("table", { name: "Operator audit log" });
  });

  it("says a 403 is a role, in the server's words, and offers no retry", async () => {
    vi.spyOn(client, "listAudit").mockRejectedValue(
      new ApiError(403, "This action needs one of these operator roles: owner, support", { status: 403 }),
    );
    renderConsole("/audit");
    const alert = await findAlert();
    expect(alert.textContent).toContain("You can't view this");
    expect(within(alert).queryByRole("button", { name: "Try again" })).toBeNull();
  });
});

describe("Audit — by role", () => {
  it("opens for support, who investigate what operators did", async () => {
    renderConsole("/audit", ["support"]);
    expect(await screen.findByRole("table", { name: "Operator audit log" })).toBeDefined();
  });

  it("does not open for billing_ops, and reads nothing", async () => {
    const read = vi.spyOn(client, "listAudit");
    renderConsole("/audit", ["billing_ops"]);

    expect(await screen.findByText("You can't open the audit log")).toBeDefined();
    expect(screen.getByText(/It is open to: Owner, Support\./)).toBeDefined();
    expect(read).not.toHaveBeenCalled();
  });
});
