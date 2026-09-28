/**
 * The printable label sheet for one zone — client-rendered, no server PDF endpoint in F1. Covers
 * the missing-zone, not-found and populated states; barcode-present renders a `QrCode`, and a
 * location with no barcode falls back to readable text (F1's human-typed fallback).
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import type { LocationOut, WarehouseOut } from "@/api/types";
import { WarehouseLocationLabelsPage } from "./WarehouseLocationLabels";

const getWarehouseById = vi.fn();
const listLocations = vi.fn();

vi.mock("@/api/client", () => ({
  getWarehouseById: (...args: unknown[]) => getWarehouseById(...args),
  listLocations: (...args: unknown[]) => listLocations(...args),
}));

const WAREHOUSE: WarehouseOut = { id: "wh-1", code: "WH-1", name: "Bhiwandi DC", location: "" };

const ZONE: LocationOut = {
  id: "loc-zone",
  warehouse_id: "wh-1",
  parent_id: null,
  kind: "zone",
  code: "ZONE-A",
  name: "Zone A",
  is_default: false,
  barcode: "LOC-ZONE-A",
  pick_sequence: 1,
  max_weight_grams: null,
  max_volume_milli: null,
  storage_type: null,
};

const BIN_NO_BARCODE: LocationOut = {
  id: "loc-bin",
  warehouse_id: "wh-1",
  parent_id: "loc-zone",
  kind: "bin",
  code: "ZONE-A-B1",
  name: "Bin 1",
  is_default: false,
  barcode: null,
  pick_sequence: 1,
  max_weight_grams: null,
  max_volume_milli: null,
  storage_type: null,
};

function renderPage(path: string) {
  const router = createMemoryRouter(
    [{ path: "/warehouses/:id/locations/labels", element: <WarehouseLocationLabelsPage /> }],
    { initialEntries: [path] },
  );
  render(<RouterProvider router={router} />);
}

describe("WarehouseLocationLabelsPage", () => {
  beforeEach(() => {
    getWarehouseById.mockReset();
    listLocations.mockReset();
    // The default for every test: the tree screen's own loaders run unconditionally (a missing
    // `zone` param is a display-time decision, not a fetch-time one), so every test needs a
    // resolved value here even where the assertion is about a state that ignores it.
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockResolvedValue([]);
  });

  it("says a zone is required when the link carries none", async () => {
    renderPage("/warehouses/wh-1/locations/labels");
    expect(await screen.findByText("No zone specified")).toBeDefined();
  });

  it("says the zone could not be found for a zone id that matches nothing", async () => {
    renderPage("/warehouses/wh-1/locations/labels?zone=missing");
    expect(await screen.findByText("That zone could not be found")).toBeDefined();
  });

  it("renders one label per location in the zone, QR-coded when barcoded and text-only when not", async () => {
    listLocations.mockResolvedValue([ZONE, BIN_NO_BARCODE]);
    renderPage("/warehouses/wh-1/locations/labels?zone=loc-zone");

    expect(await screen.findByText("Labels — Zone A (Bhiwandi DC)")).toBeDefined();
    expect(screen.getByText("2 labels, one per location in this zone.")).toBeDefined();
    expect(screen.getByRole("img", { name: "Barcode for Zone A" })).toBeDefined();
    expect(screen.getByText("No barcode set")).toBeDefined();
    expect(screen.getByRole("button", { name: "Print" })).toHaveProperty("disabled", false);
  });
});
