/**
 * Plans & Pricing: the global catalog, and the one write an operator can make from it — a bespoke,
 * audience-of-one price. Plan versions are immutable, so what the screen shows is a set of frozen
 * versions grouped by product, and what it lets an operator do is add a version, never edit one.
 *
 * The screen reads a catalog the operator API does not yet serve (see `api/contract.test.ts`), so
 * the states where that read fails are the ones a production operator meets first.
 */

import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import { PLAN_VERSIONS, PRODUCTS } from "@/api/fixtures";
import { findAlert, renderConsole } from "@/testing-render";

/** The card a product's versions are in. */
function section(name: string): HTMLElement {
  const heading = screen.getByRole("heading", { level: 2, name });
  const card = heading.closest(".rounded-card");
  if (!(card instanceof HTMLElement)) throw new Error(`No section for ${name}`);
  return card;
}

function versionRows(name: string): HTMLTableRowElement[] {
  return [...section(name).querySelectorAll<HTMLTableRowElement>("tbody tr")];
}

describe("Plans — populated", () => {
  it("groups the catalog by product, each with its description", async () => {
    renderConsole("/plans");
    expect(await screen.findByRole("heading", { level: 1, name: "Plans & Pricing" })).toBeDefined();
    await screen.findByRole("heading", { level: 2, name: "OpsGrids HR" });

    for (const product of PRODUCTS) {
      expect(screen.getByRole("heading", { level: 2, name: product.name })).toBeDefined();
      expect(screen.getByText(product.description)).toBeDefined();
    }
  });

  it("shows every version of a product, newest first within a plan, with its state, audience and price", async () => {
    renderConsole("/plans");
    await screen.findByRole("heading", { level: 2, name: "OpsGrids HR" });

    const hr = PLAN_VERSIONS.filter((v) => v.product_key === "hrms");
    expect(versionRows("OpsGrids HR")).toHaveLength(hr.length);
    const text = section("OpsGrids HR").textContent ?? "";
    expect(text.indexOf("v3")).toBeLessThan(text.indexOf("v2"));
    expect(text).toContain("Grandfathered");
    expect(text).toContain("Published");
    expect(text).toContain("Public");
    expect(text).toContain("₹45,000 / mo");
    // A per-seat price is a tag beside the flat one, not a second row.
    expect(text).toContain("₹400 / seat");
  });

  it("names the customer a bespoke version is restricted to", async () => {
    renderConsole("/plans");
    await screen.findByRole("heading", { level: 2, name: "OpsGrids Trading Desk" });
    expect(section("OpsGrids Trading Desk").textContent).toContain("Northwind Metals");
  });

  it("names the products a bundle version includes", async () => {
    renderConsole("/plans");
    await screen.findByRole("heading", { level: 2, name: "OpsGrids Suite" });
    expect(section("OpsGrids Suite").textContent).toContain("Bundles");
  });
});

describe("Plans — narrowing", () => {
  it("narrows to one product, and shows which is on", async () => {
    const user = userEvent.setup();
    renderConsole("/plans");
    await screen.findByRole("heading", { level: 2, name: "OpsGrids HR" });

    const group = screen.getByRole("group", { name: "Product" });
    await user.click(within(group).getByRole("button", { name: "OpsGrids CRM" }));

    expect(within(group).getByRole("button", { name: "OpsGrids CRM" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("heading", { level: 2, name: "OpsGrids CRM" })).toBeDefined();
    expect(screen.queryByRole("heading", { level: 2, name: "OpsGrids HR" })).toBeNull();
  });

  it("hides drafts on request, and says a product with nothing left has nothing", async () => {
    const user = userEvent.setup();
    renderConsole("/plans");
    await screen.findByRole("heading", { level: 2, name: "OpsGrids HR" });
    const drafted = PLAN_VERSIONS.filter((v) => v.state === "draft");
    expect(drafted.length).toBeGreaterThan(0);
    const before = document.querySelectorAll("tbody tr").length;
    expect(screen.getAllByText("Draft").length).toBeGreaterThan(0);

    const toggle = screen.getByRole("checkbox", { name: "Show drafts" });
    expect(toggle).toHaveProperty("checked", true);
    await user.click(toggle);

    expect(screen.queryAllByText("Draft")).toHaveLength(0);
    expect(document.querySelectorAll("tbody tr").length).toBe(before - drafted.length);
  });
});

describe("Plans — the bespoke price action", () => {
  async function openBespoke() {
    const user = userEvent.setup();
    const utils = renderConsole("/plans");
    await screen.findByRole("heading", { level: 2, name: "OpsGrids HR" });
    const opener = screen.getByRole("button", { name: "Bespoke price" });
    await waitFor(() => expect(opener.hasAttribute("disabled")).toBe(false));
    await user.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "Create a bespoke price" });
    return { user, dialog, ...utils };
  }

  it("is held until the catalog and the customers it needs have loaded", async () => {
    vi.spyOn(client, "listOrganizations").mockReturnValue(new Promise(() => undefined));
    renderConsole("/plans");
    await screen.findByRole("heading", { level: 2, name: "OpsGrids HR" });
    expect(screen.getByRole("button", { name: "Bespoke price" }).hasAttribute("disabled")).toBe(true);
  });

  it("asks which customer, product and base plan, and focuses the customer", async () => {
    const { dialog } = await openBespoke();

    expect(within(dialog).getByText("An audience-of-one plan version, pinned to a single customer. No shared plan is re-priced.")).toBeDefined();
    expect(document.activeElement).toBe(within(dialog).getByLabelText("Customer"));
    expect(within(dialog).getByLabelText("Product")).toBeDefined();
    expect(within(dialog).getByLabelText("Base plan to clone")).toBeDefined();
  });

  it("says what is missing on its own field, and sends nothing", async () => {
    const post = vi.spyOn(client, "postOverride");
    const { user, dialog } = await openBespoke();

    await user.click(within(dialog).getByRole("button", { name: "Create bespoke price" }));

    expect(within(dialog).getByText("Enter a price in rupees, like 420000.")).toBeDefined();
    expect(within(dialog).getByText("Give a reason — it goes into the audit log.")).toBeDefined();
    expect(post).not.toHaveBeenCalled();
  });

  it("creates the version for the customer chosen, in integer paise, then says so and re-reads the catalog", async () => {
    const post = vi.spyOn(client, "postOverride");
    const catalog = vi.spyOn(client, "getCatalog");
    const { user, dialog } = await openBespoke();

    const customer = within(dialog).getByLabelText("Customer") as HTMLSelectElement;
    const chosen = [...customer.options].find((o) => o.textContent === "Kaveri Steel");
    if (chosen === undefined) throw new Error("Kaveri Steel not offered");
    await user.selectOptions(customer, chosen.value);
    await user.type(within(dialog).getByLabelText("Bespoke monthly price (₹)"), "420000");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Negotiated rate");
    expect(within(dialog).getByText(/Pins Kaveri Steel to a new version at/)).toBeDefined();
    await user.click(within(dialog).getByRole("button", { name: "Create bespoke price" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0]?.[0]).toMatchObject({
      kind: "bespoke_price",
      organization_id: "org_d4c33f",
      amount_minor: 42_000_000,
      currency: "INR",
      reason: "Negotiated rate",
    });
    expect(await screen.findByText("Bespoke version created.")).toBeDefined();
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(catalog).toHaveBeenCalledTimes(2));
  });

  it("keeps the dialog on a refusal, with the server's sentence", async () => {
    vi.spyOn(client, "postOverride").mockRejectedValueOnce(
      new ApiError(503, "catalog-service is unavailable", { status: 503, detail: "catalog-service is unavailable" }),
    );
    const { user, dialog } = await openBespoke();

    await user.type(within(dialog).getByLabelText("Bespoke monthly price (₹)"), "420000");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Negotiated rate");
    await user.click(within(dialog).getByRole("button", { name: "Create bespoke price" }));

    expect(await within(dialog).findByText("catalog-service is unavailable")).toBeDefined();
    expect((within(dialog).getByLabelText("Bespoke monthly price (₹)") as HTMLInputElement).value).toBe("420000");
  });

  it("is offered to billing_ops, who may open the screen and runs pricing overrides", async () => {
    renderConsole("/plans", ["billing_ops"]);
    await screen.findByRole("heading", { level: 2, name: "OpsGrids HR" });
    expect(screen.getByRole("button", { name: "Bespoke price" })).toBeDefined();
  });
});

describe("Plans — every state of the read", () => {
  it("holds the layout with placeholders while it loads", async () => {
    vi.spyOn(client, "getCatalog").mockReturnValue(new Promise(() => undefined));
    renderConsole("/plans");
    await screen.findByRole("heading", { level: 1, name: "Plans & Pricing" });
    expect(document.querySelectorAll(".animate-pulse")).toHaveLength(3);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says the catalog is empty when it is, rather than showing nothing", async () => {
    vi.spyOn(client, "getCatalog").mockResolvedValue({ products: [], plans: [], versions: [] });
    renderConsole("/plans");
    expect(await screen.findByText("The catalog is empty")).toBeDefined();
    expect(screen.getByText("No product has a plan yet, so there is nothing to price.")).toBeDefined();
  });

  it("says a catalog the operator API does not serve is not found, and offers no retry that could not help", async () => {
    vi.spyOn(client, "getCatalog").mockRejectedValue(
      new ApiError(404, "Not found", { status: 404, detail: "Not found", request_id: "cat-404" }),
    );
    renderConsole("/plans");

    const alert = await findAlert();
    expect(alert.textContent).toContain("Not found");
    expect(alert.textContent).toContain("Reference: cat-404");
    expect(within(alert).queryByRole("button", { name: "Try again" })).toBeNull();
    // The action that needs the catalog is held, not offered and then failed.
    expect(screen.getByRole("button", { name: "Bespoke price" }).hasAttribute("disabled")).toBe(true);
  });

  it("says a real failure is one, and retries it", async () => {
    const user = userEvent.setup();
    const read = vi
      .spyOn(client, "getCatalog")
      .mockRejectedValueOnce(new ApiError(500, "catalog-service is down", { status: 500 }));
    renderConsole("/plans");

    const alert = await findAlert();
    expect(alert.textContent).toContain("Couldn't load the catalog");

    read.mockRestore();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { level: 2, name: "OpsGrids HR" })).toBeDefined();
  });
});

describe("Plans — for a role that may not open it", () => {
  it("says so and reads nothing", async () => {
    const read = vi.spyOn(client, "getCatalog");
    renderConsole("/plans", ["support"]);

    expect(await screen.findByText("You can't open plans and pricing")).toBeDefined();
    expect(screen.getByText(/It is open to: Owner, Billing ops\./)).toBeDefined();
    expect(read).not.toHaveBeenCalled();
  });
});
