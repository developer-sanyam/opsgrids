/**
 * A real list screen with the real hook and the real dialog, nothing stood in but the gateway:
 * what `importExportWiring.test.tsx` checks against a stand-in (the screen passes `onImported`,
 * and that reloads its list) shown to work when the engine is the one calling it. The screen runs
 * under `StrictMode`, as the app does, which mounts, unmounts and mounts the hook once more.
 */
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { WarehouseOut } from "@/api/types";
import { installBrowserGaps, jsonBody, openDialog, stat, uploadFile, workbookFile } from "@/data/importExport/dialogTestKit";
import { pageAt, requestsTo, stubGateway } from "@/data/importExport/entityTestKit";
import { renderScreen } from "@/test-utils/renderScreen";
import { WarehousesPage } from "./Warehouses";

const downloads = installBrowserGaps();

const LIST = "/api/v1/operations/warehouses";
const SLOW = { timeout: 8_000 };
const GRANTS = ["operations.inventory.warehouse.read:ORG", "operations.inventory.warehouse.write:ORG"];

beforeEach(() => {
  downloads.length = 0;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** A gateway that remembers what it was asked to create, so the list it serves next includes it. */
function warehouseGateway() {
  const stored: WarehouseOut[] = [{ id: "wh-1", code: "WH-MAIN", name: "Main Warehouse", location: "Bhiwandi" }];
  const recorded = stubGateway(({ url, init }) => {
    if ((init.method ?? "GET") === "POST") {
      const { code, name, location } = jsonBody(init);
      stored.push({
        id: `wh-${String(code)}`,
        code: String(code),
        name: String(name),
        location: typeof location === "string" ? location : "",
      });
      return { status: 201, body: { id: `wh-${String(code)}` } };
    }
    return { body: pageAt([stored], Number(url.searchParams.get("page") ?? "1")) };
  });
  return recorded;
}

describe("the Warehouses screen with the real import dialog", () => {
  it(
    "reloads its list when the run ends, and the dialog stays open on the result",
    async () => {
      const user = userEvent.setup();
      const recorded = warehouseGateway();
      renderScreen(
        <StrictMode>
          <WarehousesPage />
        </StrictMode>,
        GRANTS,
      );
      expect((await screen.findAllByText("Main Warehouse")).length).toBeGreaterThan(0);
      expect(screen.queryByText("North Hub")).toBeNull();
      const loadsBefore = requestsTo(recorded, "GET", LIST).length;

      await openDialog(user, "warehouses");
      await uploadFile(
        user,
        await workbookFile(
          "warehouses.xlsx",
          ["Warehouse code *", "Warehouse name *", "Location"],
          [
            ["WH-NORTH", "North Hub", "Ludhiana, Punjab"],
            ["WH-EAST", "East Hub", null],
          ],
        ),
      );
      const review = await screen.findByRole("dialog", { name: "Review your file" }, SLOW);
      expect(stat(review, "Ready to import")).toBe("2");
      await user.click(within(review).getByRole("button", { name: "Import 2 rows" }));

      const finished = await screen.findByRole("dialog", { name: "Import finished" }, SLOW);
      expect(stat(finished, "Created")).toBe("2");
      expect(
        requestsTo(recorded, "POST", LIST).map((request) => jsonBody(request.init)),
      ).toEqual([
        { code: "WH-NORTH", name: "North Hub", location: "Ludhiana, Punjab" },
        { code: "WH-EAST", name: "East Hub" },
      ]);

      // The list was fetched again once the run ended, and the new rows are on the page behind the dialog.
      await waitFor(() => expect(requestsTo(recorded, "GET", LIST).length).toBeGreaterThan(loadsBefore), SLOW);
      await waitFor(() => expect(screen.getAllByText("North Hub").length).toBeGreaterThan(0), SLOW);
      expect(screen.getAllByText("East Hub").length).toBeGreaterThan(0);
      expect(screen.getByRole("dialog", { name: "Import finished" })).toBeTruthy();
      expect(stat(screen.getByRole("dialog", { name: "Import finished" }), "Created")).toBe("2");
    },
    10_000,
  );

  it("leaves its list alone when the run created nothing", async () => {
    const user = userEvent.setup();
    const recorded = stubGateway(({ url, init }) => {
      if ((init.method ?? "GET") === "POST") {
        return { status: 409, body: { title: "Conflict", status: 409, detail: "A warehouse coded 'WH-MAIN' already exists" } };
      }
      const rows: WarehouseOut[] = [{ id: "wh-1", code: "WH-MAIN", name: "Main Warehouse", location: "" }];
      return { body: pageAt([rows], Number(url.searchParams.get("page") ?? "1")) };
    });
    renderScreen(
      <StrictMode>
        <WarehousesPage />
      </StrictMode>,
      GRANTS,
    );
    await screen.findAllByText("Main Warehouse");
    const loadsBefore = requestsTo(recorded, "GET", LIST).length;

    await openDialog(user, "warehouses");
    await uploadFile(
      user,
      await workbookFile("warehouses.xlsx", ["Warehouse code *", "Warehouse name *"], [["WH-MAIN", "Main Warehouse"]]),
    );
    const review = await screen.findByRole("dialog", { name: "Review your file" }, SLOW);
    await user.click(within(review).getByRole("button", { name: "Import 1 row" }));
    const finished = await screen.findByRole("dialog", { name: "Import finished" }, SLOW);

    expect(stat(finished, "Skipped, already exist")).toBe("1");
    expect(within(finished).getByText("A warehouse coded 'WH-MAIN' already exists")).toBeTruthy();
    expect(requestsTo(recorded, "GET", LIST)).toHaveLength(loadsBefore);
  }, 10_000);
});
