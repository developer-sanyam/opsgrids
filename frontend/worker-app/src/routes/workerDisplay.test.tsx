/**
 * The task screens show what the server resolved for the caller's own task — the order number, the
 * bin's code and the item's name and unit — and fall back to the shortened id only when a field is
 * absent. The same screens also keep their primary action at 48px and, where there is a form,
 * pinned to the foot of the viewport.
 */
import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { json, makeLine, makePackage, makeTask, page, renderScreen, stubGateway } from "@/worker/testKit";

import { PackPage } from "./Pack";
import { PickConfirmPage } from "./PickConfirm";
import { TaskDetailPage } from "./TaskDetail";

afterEach(() => {
  vi.unstubAllGlobals();
});

const TASK_GRANTS = ["operations.fulfilment.task.read:ORG", "operations.fulfilment.task.execute:OWN"];
const ORDER_ID = "11111111-2222-3333-4444-555555555555";

const NAMED_LINE = makeLine({
  quantity: "12.5",
  item_name: "Cotton roll",
  item_unit: "m",
  source_location_code: "A-01-03",
  source_location_name: "Rack A, bay 1",
  destination_location_code: "STG-1",
});

const NAMED_TASK = makeTask({ order_number: "ORD-000042", lines: [NAMED_LINE] });

describe("TaskDetailPage display fields", () => {
  it("shows the order number, the item's name and unit, and the bin's code instead of ids", async () => {
    stubGateway({ "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(NAMED_TASK) });
    renderScreen(<TaskDetailPage />, { at: "/tasks/task-1", path: "/tasks/:taskId", grants: TASK_GRANTS });

    expect(await screen.findByRole("heading", { name: "Order ORD-000042" })).toBeTruthy();
    expect(screen.getByText("Cotton roll")).toBeTruthy();
    expect(screen.getByText("SKU-100")).toBeTruthy();
    expect(screen.getByText(/12\.5 m requested/)).toBeTruthy();
    expect(screen.getByText(/from A-01-03 · Rack A, bay 1/)).toBeTruthy();
    // None of the id fragments the screen used to show.
    expect(screen.queryByText(/11111111/)).toBeNull();
    expect(screen.queryByText(/loc-bin-/)).toBeNull();
  });

  it("falls back to the shortened ids when the server sent none of them", async () => {
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(makeTask({ lines: [makeLine()] })),
    });
    renderScreen(<TaskDetailPage />, { at: "/tasks/task-1", path: "/tasks/:taskId", grants: TASK_GRANTS });

    expect(await screen.findByRole("heading", { name: "Order 11111111" })).toBeTruthy();
    expect(screen.getByText("SKU-100")).toBeTruthy();
    expect(screen.getByText(/from loc-bin-/)).toBeTruthy();
  });

  it("keeps the Confirm link at 48px", async () => {
    stubGateway({ "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(NAMED_TASK) });
    renderScreen(<TaskDetailPage />, { at: "/tasks/task-1", path: "/tasks/:taskId", grants: TASK_GRANTS });

    const confirm = await screen.findByRole("link", { name: /Confirm/ });
    expect(confirm.className).toContain("h-12");
    expect(confirm.className).not.toContain("h-10");
  });
});

describe("PickConfirmPage display fields", () => {
  it("names the order, the bin and the item, with the remaining quantity in its unit", async () => {
    stubGateway({ "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(NAMED_TASK) });
    renderScreen(<PickConfirmPage />, {
      at: "/tasks/task-1/lines/line-1/pick",
      path: "/tasks/:taskId/lines/:lineId/pick",
      grants: TASK_GRANTS,
    });

    expect(await screen.findByText("Order ORD-000042 · from A-01-03 · Rack A, bay 1")).toBeTruthy();
    expect(screen.getByText("Cotton roll")).toBeTruthy();
    expect(screen.getByText("12.5 m remaining on this line")).toBeTruthy();
  });

  it("falls back to the SKU and the shortened ids", async () => {
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(makeTask({ lines: [makeLine()] })),
    });
    renderScreen(<PickConfirmPage />, {
      at: "/tasks/task-1/lines/line-1/pick",
      path: "/tasks/:taskId/lines/:lineId/pick",
      grants: TASK_GRANTS,
    });

    expect(await screen.findByText(/Order 11111111 · from loc-bin-/)).toBeTruthy();
    expect(screen.getByText("SKU-100")).toBeTruthy();
  });

  it("pins the confirm button in the sticky action bar at 48px", async () => {
    stubGateway({ "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(NAMED_TASK) });
    renderScreen(<PickConfirmPage />, {
      at: "/tasks/task-1/lines/line-1/pick",
      path: "/tasks/:taskId/lines/:lineId/pick",
      grants: TASK_GRANTS,
    });

    const button = await screen.findByRole("button", { name: "Confirm pick" });
    expect(button.className).toContain("min-h-12");
    expect(button.closest(".shell-sticky-action")).not.toBeNull();
  });
});

describe("PackPage display fields", () => {
  const PACK_GRANTS = ["operations.fulfilment.pack.read:ORG", "operations.fulfilment.pack.execute:ORG"];

  function stubPack(task = NAMED_TASK) {
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks": () =>
        json(page([{ ...task, status: "PICKED", lines: [{ ...NAMED_LINE, picked: "12.5", status: "picked" }] }])),
      "GET /api/v1/operations/fulfilment/packages": () => json(page([])),
      "GET /api/v1/operations/fulfilment/packaging-materials": () => json(page([])),
    });
  }

  it("heads the page with the order number and labels each line by item name and unit", async () => {
    stubPack();
    renderScreen(<PackPage />, { at: `/orders/${ORDER_ID}/pack`, path: "/orders/:orderId/pack", grants: PACK_GRANTS });

    expect(await screen.findByText("Order ORD-000042")).toBeTruthy();
    expect(screen.getByLabelText("Cotton roll")).toBeTruthy();
    expect(screen.getByText("12.5 m staged · SKU-100")).toBeTruthy();
  });

  it("pins Confirm package in the sticky action bar at 48px", async () => {
    stubPack();
    renderScreen(<PackPage />, { at: `/orders/${ORDER_ID}/pack`, path: "/orders/:orderId/pack", grants: PACK_GRANTS });

    const button = await screen.findByRole("button", { name: "Confirm package" });
    expect(button.className).toContain("min-h-12");
    expect(button.closest(".shell-sticky-action")).not.toBeNull();
  });

  it("falls back to the shortened order id and the SKU when nothing was resolved", async () => {
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks": () =>
        json(page([makeTask({ status: "PICKED", lines: [makeLine({ picked: "10", status: "picked" })] })])),
      "GET /api/v1/operations/fulfilment/packages": () => json(page([])),
      "GET /api/v1/operations/fulfilment/packaging-materials": () => json(page([])),
    });
    renderScreen(<PackPage />, { at: `/orders/${ORDER_ID}/pack`, path: "/orders/:orderId/pack", grants: PACK_GRANTS });

    expect(await screen.findByText("Order 11111111")).toBeTruthy();
    expect(screen.getByLabelText("SKU-100")).toBeTruthy();
  });

  it("names the item on a package already packed", async () => {
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks": () => json(page([NAMED_TASK])),
      "GET /api/v1/operations/fulfilment/packages": () =>
        json(
          page([
            makePackage({
              lines: [
                {
                  id: "pl-1",
                  pick_task_line_id: "line-1",
                  inventory_item_id: "item-1",
                  sku: "SKU-100",
                  quantity: "12.5",
                  item_name: "Cotton roll",
                  item_unit: "m",
                },
              ],
            }),
          ]),
        ),
      "GET /api/v1/operations/fulfilment/packaging-materials": () => json(page([])),
    });
    renderScreen(<PackPage />, { at: `/orders/${ORDER_ID}/pack`, path: "/orders/:orderId/pack", grants: PACK_GRANTS });

    const packages = await screen.findByText("Packages so far");
    const card = packages.closest("div");
    if (!card) throw new Error("packages card not found");
    expect(within(card).getByText("Cotton roll")).toBeTruthy();
    expect(within(card).getByText("12.5 m")).toBeTruthy();
  });
});
