/**
 * The camera is never available in this test environment (jsdom has no `BarcodeDetector` and no
 * `getUserMedia`), which is exactly the "no camera" reliability case the mission brief asks for —
 * manual entry is what every one of these exercises, the same path a real denied-permission or
 * no-camera phone falls back to.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { json, makeLine, makeTask, renderScreen, stubGateway } from "@/worker/testKit";

import { ScanPage } from "./Scan";

const AT = "/tasks/task-1/scan";
const PATH = "/tasks/:taskId/scan";
const GRANTS = ["operations.fulfilment.task.execute:OWN"];

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("ScanPage", () => {
  it("manual entry always works, camera or not: a matching item scan offers to confirm its pick", async () => {
    const task = makeTask({ lines: [makeLine({ sku: "SKU-100" })] });
    let scanBody: unknown;
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(task),
      "POST /api/v1/operations/fulfilment/tasks/task-1/scan": (call) => {
        scanBody = call.body;
        return json(task);
      },
    });
    renderScreen(<ScanPage />, { at: AT, path: PATH, grants: GRANTS });

    const field = await screen.findByLabelText("Item barcode or SKU");
    await userEvent.type(field, "SKU-100");
    await userEvent.click(screen.getByRole("button", { name: "Log" }));

    expect(await screen.findByText("Matched SKU-100.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Confirm pick for SKU-100" })).toBeTruthy();
    await waitFor(() => expect(scanBody).toEqual({ barcode: "SKU-100", inventory_item_id: "item-1" }));
  });

  it("a wrong item scan is a clear, specific error — never a silent no-op", async () => {
    const task = makeTask({ lines: [makeLine({ sku: "SKU-100" })] });
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(task),
      "POST /api/v1/operations/fulfilment/tasks/task-1/scan": () => json(task),
    });
    renderScreen(<ScanPage />, { at: AT, path: PATH, grants: GRANTS });

    const field = await screen.findByLabelText("Item barcode or SKU");
    await userEvent.type(field, "SKU-WRONG");
    await userEvent.click(screen.getByRole("button", { name: "Log" }));

    expect(await screen.findByText('No line on this task expects "SKU-WRONG".')).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Confirm pick/ })).toBeNull();
  });

  it("offline: a scan is queued rather than lost, and syncs once back online", async () => {
    const task = makeTask({ lines: [makeLine({ sku: "SKU-100" })] });
    let scanCalls = 0;
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(task),
      "POST /api/v1/operations/fulfilment/tasks/task-1/scan": () => {
        scanCalls += 1;
        return json(task);
      },
    });
    vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);

    renderScreen(<ScanPage />, { at: AT, path: PATH, grants: GRANTS });

    const field = await screen.findByLabelText("Item barcode or SKU");
    await userEvent.type(field, "SKU-100");
    await userEvent.click(screen.getByRole("button", { name: "Log" }));

    expect(await screen.findByText("1 scan waiting to sync")).toBeTruthy();
    expect(scanCalls).toBe(0);

    vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(true);
    window.dispatchEvent(new Event("online"));

    await waitFor(() => expect(scanCalls).toBe(1));
    await waitFor(() => expect(screen.queryByText(/waiting to sync/)).toBeNull());
  });
});
