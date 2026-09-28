import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { json, makeLine, makePackage, makeTask, page, renderScreen, stubGateway } from "@/worker/testKit";

import { PackPage } from "./Pack";

const AT = "/orders/order-1/pack";
const PATH = "/orders/:orderId/pack";
const GRANTS = ["operations.fulfilment.pack.read:ORG", "operations.fulfilment.pack.execute:ORG"];

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PackPage", () => {
  it("nothing staged yet: an honest empty state, not a form with nothing to fill in", async () => {
    const task = makeTask({ order_id: "order-1", lines: [makeLine({ picked: "0" })] });
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks": () => json(page([task])),
      "GET /api/v1/operations/fulfilment/packages": () => json(page([])),
      "GET /api/v1/operations/fulfilment/packaging-materials": () => json(page([])),
    });
    renderScreen(<PackPage />, { at: AT, path: PATH, grants: GRANTS });

    expect(await screen.findByText("Nothing staged to pack yet")).toBeTruthy();
  });

  it("packs a fully picked line and shows the confirmed package", async () => {
    const task = makeTask({ order_id: "order-1", lines: [makeLine({ quantity: "10", picked: "10" })] });
    let confirmBody: unknown;
    let packagesReturned = page<ReturnType<typeof makePackage>>([]);
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks": () => json(page([task])),
      "GET /api/v1/operations/fulfilment/packages": () => json(packagesReturned),
      "GET /api/v1/operations/fulfilment/packaging-materials": () => json(page([])),
      "GET /api/v1/operations/locations/by-barcode/550e8400-e29b-41d4-a716-446655440000": () =>
        json({ title: "Forbidden", status: 403, detail: "Missing permission" }, 403),
      "POST /api/v1/operations/fulfilment/packages": (call) => {
        confirmBody = call.body;
        const created = makePackage({
          lines: [{ id: "pl-1", pick_task_line_id: "line-1", inventory_item_id: "item-1", sku: "SKU-100", quantity: "10" }],
        });
        packagesReturned = page([created]);
        return json(created, 201);
      },
    });
    renderScreen(<PackPage />, { at: AT, path: PATH, grants: GRANTS });

    const destination = await screen.findByLabelText("Destination location");
    await userEvent.type(destination, "550e8400-e29b-41d4-a716-446655440000");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByText(/Destination:/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Confirm package" }));

    await waitFor(() =>
      expect(confirmBody).toEqual({
        order_id: "order-1",
        destination_location_id: "550e8400-e29b-41d4-a716-446655440000",
        lines: [{ pick_task_line_id: "line-1", quantity: "10" }],
      }),
    );
    expect(await screen.findByText("Packages so far")).toBeTruthy();
  });

  it("every picked line already packed: the terminal ready-for-handover state", async () => {
    const task = makeTask({ order_id: "order-1", lines: [makeLine({ quantity: "10", picked: "10" })] });
    const existingPackage = makePackage({
      lines: [{ id: "pl-1", pick_task_line_id: "line-1", inventory_item_id: "item-1", sku: "SKU-100", quantity: "10" }],
    });
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks": () => json(page([task])),
      "GET /api/v1/operations/fulfilment/packages": () => json(page([existingPackage])),
      "GET /api/v1/operations/fulfilment/packaging-materials": () => json(page([])),
    });
    renderScreen(<PackPage />, { at: AT, path: PATH, grants: GRANTS });

    expect(await screen.findByText("Packed — ready for handover")).toBeTruthy();
    expect(screen.queryByLabelText("Destination location")).toBeNull();
  });

  it("permission-denied: a caller with no pack.read grant is told plainly, never a blank form", async () => {
    stubGateway({});
    renderScreen(<PackPage />, { at: AT, path: PATH, grants: [] });

    expect(await screen.findByText("You can't open packing right now")).toBeTruthy();
  });
});
