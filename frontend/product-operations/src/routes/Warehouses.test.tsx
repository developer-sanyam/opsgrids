/**
 * The Warehouses directory's way into the map: one button, for everyone who can read the list. The
 * list itself, its import and its dialogs are covered where they have always been
 * (`importExportScreen.test.tsx`).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { WarehouseOut } from "@/api/types";
import { installBrowserGaps } from "@/data/importExport/dialogTestKit";
import { pageAt, stubGateway } from "@/data/importExport/entityTestKit";
import { renderRoutes } from "@/test-utils/renderRoutes";
import { WarehousesPage } from "./Warehouses";

installBrowserGaps();

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WarehousesPage", () => {
  it("links to the map view for a reader, who cannot write", async () => {
    const user = userEvent.setup();
    stubGateway(({ url }) => {
      const rows: WarehouseOut[] = [{ id: "wh-1", code: "WH-MAIN", name: "Main Warehouse", location: "Bhiwandi" }];
      return { body: pageAt([rows], Number(url.searchParams.get("page") ?? "1")) };
    });
    const { where } = renderRoutes(
      [
        { path: "/warehouses", element: <WarehousesPage /> },
        { path: "/warehouses/map", element: <h1>The map page</h1> },
      ],
      "/warehouses",
      ["operations.inventory.warehouse.read:ORG"],
    );

    await screen.findAllByText("Main Warehouse");
    expect(screen.queryByRole("button", { name: "New warehouse" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Map view" }));
    await waitFor(() => expect(where().pathname).toBe("/warehouses/map"));
  });
});
