/**
 * The production-order list: its four states, the status filter and search reaching the server,
 * the "New production order" gate, the create call, and the wastage-threshold panel beneath it.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { StubResponse } from "@/test-utils/stubApi";
import { problem, route, stubApi } from "@/test-utils/stubApi";
import { LOCATIONS_PAGE, WAREHOUSE_PAGE, bom, order, orderSummary, page } from "@/test-utils/manufacturingFixtures";
import { renderScreen } from "@/test-utils/renderScreen";
import { ProductionOrdersPage } from "./ProductionOrders";

const READ = "operations.manufacturing.production_order.read:ORG";
const WRITE = "operations.manufacturing.production_order.write:ORG";
const BOM_WRITE = "operations.manufacturing.bom.write:ORG";

const THRESHOLDS = route("GET", /\/wastage-thresholds$/, { body: [] });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ProductionOrdersPage", () => {
  it("shows a loading placeholder while the list is on its way", () => {
    stubApi([route("GET", /\/manufacturing\/orders$/, () => new Promise<StubResponse>(() => undefined)), THRESHOLDS]);
    renderScreen(<ProductionOrdersPage />, [READ]);
    expect(screen.getByLabelText("Loading production orders")).toBeDefined();
  });

  it("says what to do next when there are no orders", async () => {
    stubApi([route("GET", /\/manufacturing\/orders$/, { body: page([]) }), THRESHOLDS]);
    renderScreen(<ProductionOrdersPage />, [READ]);
    expect(await screen.findByText("No production orders yet")).toBeDefined();
    expect(screen.getByText("Plan the first one from a published BOM.")).toBeDefined();
  });

  it("shows the server's sentence and a retry when the list fails", async () => {
    stubApi([route("GET", /\/manufacturing\/orders$/, problem(403, "You can't read production orders.")), THRESHOLDS]);
    renderScreen(<ProductionOrdersPage />, [READ]);
    expect(await screen.findByText("You can't read production orders.")).toBeDefined();
    expect(screen.getByRole("button", { name: "Try again" })).toBeDefined();
  });

  it("lists each order with its status, quantities, due date and pinned BOM version", async () => {
    stubApi([
      route("GET", /\/manufacturing\/orders$/, {
        body: page([orderSummary({ status: "IN_PROGRESS", produced_quantity: "4.5" })]),
      }),
      THRESHOLDS,
    ]);
    renderScreen(<ProductionOrdersPage />, [READ]);

    const link = await screen.findByRole("link", { name: "PO-0001" });
    expect(link.getAttribute("href")).toBe("/manufacturing/orders/ord-1");
    const row = link.closest("tr");
    expect(row).not.toBeNull();
    const cells = within(row as HTMLElement);
    expect(cells.getByText("In progress")).toBeDefined();
    expect(cells.getByText("10 pc")).toBeDefined();
    expect(cells.getByText("4.5 pc")).toBeDefined();
    expect(cells.getByText("20 Oct 2026")).toBeDefined();
    expect(cells.getByText("SHIRT-01 · v1")).toBeDefined();
  });

  it("sends the status filter and the search to the server", async () => {
    const api = stubApi([route("GET", /\/manufacturing\/orders$/, { body: page([orderSummary()]) }), THRESHOLDS]);
    const user = userEvent.setup();
    renderScreen(<ProductionOrdersPage />, [READ]);
    await screen.findByText("PO-0001");

    await user.click(screen.getByRole("button", { name: "Approved" }));
    await waitFor(() => expect(api.calls("GET", /orders$/).some((r) => r.query.get("status") === "APPROVED")).toBe(true));

    await user.type(screen.getByLabelText("Search production orders"), "PO-00");
    await waitFor(() => expect(api.calls("GET", /orders$/).some((r) => r.query.get("q") === "PO-00")).toBe(true));
  });

  it("hides New production order without the write grant", async () => {
    stubApi([route("GET", /\/manufacturing\/orders$/, { body: page([orderSummary()]) }), THRESHOLDS]);
    const first = renderScreen(<ProductionOrdersPage />, [READ]);
    await screen.findByText("PO-0001");
    expect(screen.queryByRole("button", { name: "New production order" })).toBeNull();
    first.unmount();

    renderScreen(<ProductionOrdersPage />, [READ, WRITE]);
    expect(await screen.findByRole("button", { name: "New production order" })).toBeDefined();
  });

  it("plans an order with a string quantity and an Idempotency-Key", async () => {
    const api = stubApi([
      route("GET", /\/manufacturing\/orders$/, { body: page([]) }),
      route("GET", /\/manufacturing\/boms$/, { body: page([bom()]) }),
      route("GET", /\/operations\/warehouses$/, { body: WAREHOUSE_PAGE }),
      route("GET", /\/warehouses\/wh-1\/locations$/, { body: LOCATIONS_PAGE }),
      route("POST", /\/manufacturing\/orders$/, { status: 201, body: order() }),
      THRESHOLDS,
    ]);
    const user = userEvent.setup();
    renderScreen(<ProductionOrdersPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: "New production order" }));
    await user.type(screen.getByRole("combobox", { name: "BOM" }), "shirt");
    await user.click(await screen.findByRole("option", { name: /SHIRT-01/ }));
    await user.type(screen.getByLabelText(/^Quantity/), "12.5");
    await waitFor(() => expect(screen.getByLabelText(/^Output location/).hasAttribute("disabled")).toBe(false));
    await user.selectOptions(screen.getByLabelText(/^Output location/), "loc-out");
    await user.click(screen.getByRole("button", { name: "Create order" }));

    await waitFor(() => expect(api.calls("POST", /orders$/).length).toBe(1));
    const sent = api.calls("POST", /orders$/)[0];
    expect(sent?.body).toEqual({ bom_id: "bom-1", quantity: "12.5", output_location_id: "loc-out", notes: "" });
    expect(sent?.headers["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("lists wastage thresholds and lets a writer add one", async () => {
    stubApi([
      route("GET", /\/manufacturing\/orders$/, { body: page([]) }),
      route("GET", /\/wastage-thresholds$/, {
        body: [{ id: "thr-1", item_id: "item-shirt", sku: "SHIRT", wastage_type: "CUTTING", max_percent: "2.5", created_at: "2026-09-01T00:00:00Z" }],
      }),
    ]);
    const first = renderScreen(<ProductionOrdersPage />, [READ]);
    expect(await screen.findByText("2.5%")).toBeDefined();
    expect(screen.getByText("Cutting")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Add threshold" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Remove threshold/ })).toBeNull();
    first.unmount();

    renderScreen(<ProductionOrdersPage />, [READ, BOM_WRITE]);
    expect(await screen.findByRole("button", { name: "Add threshold" })).toBeDefined();
    expect(screen.getByRole("button", { name: /^Remove threshold/ })).toBeDefined();
  });

  it("refuses a threshold percent over 100 before any request is made", async () => {
    const api = stubApi([route("GET", /\/manufacturing\/orders$/, { body: page([]) }), THRESHOLDS]);
    const user = userEvent.setup();
    renderScreen(<ProductionOrdersPage />, [READ, BOM_WRITE]);

    await user.click(await screen.findByRole("button", { name: "Add threshold" }));
    await user.type(screen.getByLabelText(/^Maximum percent/), "101");
    await user.click(screen.getByRole("button", { name: "Save threshold" }));

    expect(await screen.findByText(/0 to 100 with at most two decimals/)).toBeDefined();
    expect(api.calls("POST", /wastage-thresholds$/).length).toBe(0);
  });
});
