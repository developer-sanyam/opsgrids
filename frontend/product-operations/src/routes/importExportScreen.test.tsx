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
      // Reading the file loaded the names to check against, so count the list's loads from here.
      const loadsBeforeRun = requestsTo(recorded, "GET", LIST).length;
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
      await waitFor(() => expect(requestsTo(recorded, "GET", LIST).length).toBeGreaterThan(loadsBeforeRun), SLOW);
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
        const detail = `A warehouse coded '${String(jsonBody(init).code)}' already exists`;
        const errors = [{ field: "code", code: "duplicate", message: detail }];
        return { status: 409, body: { title: "Conflict", status: 409, detail, errors } };
      }
      const rows: WarehouseOut[] = [
        { id: "wh-1", code: "WH-MAIN", name: "Main Warehouse", location: "" },
        { id: "wh-2", code: "WH-OLD", name: "Old Depot", location: "" },
      ];
      return { body: pageAt([rows], Number(url.searchParams.get("page") ?? "1")) };
    });
    renderScreen(
      <StrictMode>
        <WarehousesPage />
      </StrictMode>,
      GRANTS,
    );
    await screen.findAllByText("Main Warehouse");

    await openDialog(user, "warehouses");
    // Not one of the entity's own examples (WH-MAIN, WH-COLD): a file whose only columns match an
    // example row is an unchanged sample and is skipped without a request.
    await uploadFile(
      user,
      await workbookFile("warehouses.xlsx", ["Warehouse code *", "Warehouse name *"], [["WH-OLD", "Old Depot"]]),
    );
    const review = await screen.findByRole("dialog", { name: "Review your file" }, SLOW);
    const loadsBeforeRun = requestsTo(recorded, "GET", LIST).length;
    await user.click(within(review).getByRole("button", { name: "Import 1 row" }));
    const finished = await screen.findByRole("dialog", { name: "Import finished" }, SLOW);

    expect(stat(finished, "Skipped, already exist")).toBe("1");
    const skipped = within(within(finished).getByRole("table", { name: "Rows that were not created" }));
    expect(skipped.getByRole("row", { name: /WH-OLD/ }).textContent).toContain(
      "A warehouse coded 'WH-OLD' already exists",
    );
    expect(requestsTo(recorded, "GET", LIST)).toHaveLength(loadsBeforeRun);
  }, 10_000);
});
