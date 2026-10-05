/**
 * The sites map page. The map is an enhancement and the list is the page: every test that matters
 * here is a way for the map to be absent or to fail while the list keeps working — no key, a script
 * that errors, a key Google refuses, a chunk that will not load, a render error — plus the
 * warehouses with no position, and the slow and failing loads.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import type { WarehouseMapOut } from "@/api/types";
import { resetReports } from "@/lib/clientReport";
import { renderRoutes } from "@/test-utils/renderRoutes";
import { stubViewport } from "@/test-utils/viewport";
import { WarehousesMapPage } from "./WarehousesMap";

const getWarehousesMap = vi.fn();
const updateWarehouse = vi.fn();
const sitesMapProps = vi.fn();
const sitesMapMounts = vi.fn();
let sitesMapThrows = false;
let sitesMapChunkFailures = 0;

vi.mock("@/api/client", () => ({
  getWarehousesMap: (...args: unknown[]) => getWarehousesMap(...args),
  updateWarehouse: (...args: unknown[]) => updateWarehouse(...args),
  createWarehouse: () => Promise.reject(new Error("not used")),
}));

// Stands in for the Google map: the real one cannot render in a DOM. It exposes what the page gave
// it and lets a test make it fail the ways Google can. The factory throwing is the chunk failing to
// download: the dynamic import rejects, exactly as a network error rejects the real one.
vi.mock("@/maps/SitesMap", () => {
  if (sitesMapChunkFailures > 0) {
    sitesMapChunkFailures -= 1;
    throw new Error("Failed to fetch dynamically imported module");
  }
  return {
    default: (props: {
      sites: { id: string; name: string }[];
      selectedId: string | null;
      onSelect: (id: string | null) => void;
      onOpenFloors: (id: string) => void;
      onFailure: (reason: "script_error" | "auth_failure") => void;
    }) => {
      if (sitesMapThrows) throw new Error("render error inside the map");
      sitesMapMounts();
      sitesMapProps(props);
      return (
        <div data-testid="sites-map-stub">
          <p>map selected: {props.selectedId ?? "none"}</p>
          {props.sites.map((s) => (
            <button key={s.id} type="button" onClick={() => props.onSelect(s.id)}>
              pin {s.name}
            </button>
          ))}
          <button type="button" onClick={() => props.onFailure("auth_failure")}>
            google refuses the key
          </button>
          <button type="button" onClick={() => props.onFailure("script_error")}>
            script fails
          </button>
          <button type="button" onClick={() => props.sites[0] && props.onOpenFloors(props.sites[0].id)}>
            popup open floors
          </button>
        </div>
      );
    },
  };
});

vi.mock("@/maps/PlacesLookup", () => ({ default: () => <p>places lookup stub</p> }));

const READ = "operations.inventory.warehouse.read:ORG";
const WRITE = "operations.inventory.warehouse.write:ORG";

const SITE_A = {
  id: "wh-1",
  code: "BHW",
  name: "Bhiwandi DC",
  location: "Plot 5, MIDC, Bhiwandi",
  lat_micro: 19_300_000,
  lng_micro: 73_060_000,
  floor_count: 3,
  stocked_location_count: 120,
};
const SITE_B = { ...SITE_A, id: "wh-2", code: "PUN", name: "Pune Hub", location: "Hinjewadi, Pune", floor_count: 1, stocked_location_count: 1 };
const UNLOCATED = { id: "wh-3", code: "GOA", name: "Goa Store", location: "Margao" };

function data(overrides: Partial<WarehouseMapOut> = {}): WarehouseMapOut {
  return { sites: [SITE_A, SITE_B], without_coordinates: [UNLOCATED], ...overrides };
}

function renderMap(grants = [READ], at = "/warehouses/map") {
  return renderRoutes(
    [
      { path: "/warehouses/map", element: <WarehousesMapPage /> },
      { path: "/warehouses", element: <h1>Warehouses list</h1> },
      { path: "/warehouses/:id/floors", element: <h1>Floors of a warehouse</h1> },
    ],
    at,
    grants,
  );
}

describe("WarehousesMapPage", () => {
  beforeEach(() => {
    resetReports();
    sitesMapThrows = false;
    sitesMapChunkFailures = 0;
    getWarehousesMap.mockReset().mockResolvedValue(data());
    updateWarehouse.mockReset();
    sitesMapProps.mockReset();
    sitesMapMounts.mockReset();
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  describe("with no Maps key", () => {
    beforeEach(() => {
      vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "");
    });

    it("shows the warehouses as a list with a sentence saying why there is no map, and never loads the map", async () => {
      renderMap();
      expect(await screen.findByText("The map isn't set up here, so your warehouses are listed instead")).toBeDefined();
      expect(screen.getByRole("table", { name: "Warehouses with a location" })).toBeDefined();
      expect(screen.getByText("Bhiwandi DC")).toBeDefined();
      expect(screen.getByText("Plot 5, MIDC, Bhiwandi")).toBeDefined();
      expect(screen.queryByTestId("sites-map-stub")).toBeNull();
      expect(sitesMapMounts).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", { name: /Show on map/ })).toBeNull();
    });

    it("lists floors and stocked bins and opens a warehouse's floors from the list", async () => {
      const user = userEvent.setup();
      const { where } = renderMap();
      await screen.findByRole("table", { name: "Warehouses with a location" });
      const row = screen.getByText("Bhiwandi DC").closest("tr");
      expect(row?.textContent).toContain("3");
      expect(row?.textContent).toContain("120");

      await user.click(screen.getByRole("button", { name: "Open floors Bhiwandi DC" }));
      await waitFor(() => expect(where().pathname).toBe("/warehouses/wh-1/floors"));
    });

    it("marks the warehouse a search pointed at as selected, in words", async () => {
      renderMap([READ], "/warehouses/map?focus=wh-2");
      const row = (await screen.findByText("Pune Hub")).closest("tr");
      expect(row?.textContent).toContain("Selected");
      expect(screen.getByRole("status").textContent).toBe("Selected Pune Hub");
    });

    it("selects a warehouse from its row actions", async () => {
      const user = userEvent.setup();
      renderMap();
      await screen.findByRole("table", { name: "Warehouses with a location" });
      await user.click(screen.getByRole("button", { name: "Select Pune Hub" }));
      expect(screen.getByText("Pune Hub").closest("tr")?.textContent).toContain("Selected");
    });
  });

  describe("with a Maps key", () => {
    beforeEach(() => {
      vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "test-key");
    });

    // First on purpose: the map's lazy component is module state, as it is in the app, and once a
    // test has loaded it a later one cannot make the chunk fail. The check that the failure was
    // used up keeps this from passing vacuously if another test is ever put above it.
    it("downloads the map's chunk again on 'Try the map again' when the chunk itself failed to download", async () => {
      vi.spyOn(console, "error").mockImplementation(() => undefined);
      sitesMapChunkFailures = 1;
      const user = userEvent.setup();
      renderMap();

      expect(await screen.findByText("The map couldn't load, so your warehouses are listed instead")).toBeDefined();
      expect(sitesMapChunkFailures).toBe(0);
      expect(screen.queryByTestId("sites-map-stub")).toBeNull();
      expect(screen.getByRole("table", { name: "Warehouses with a location" })).toBeDefined();

      await user.click(screen.getByRole("button", { name: "Try the map again" }));
      expect(await screen.findByTestId("sites-map-stub")).toBeDefined();
      expect(screen.queryByText("The map couldn't load, so your warehouses are listed instead")).toBeNull();
    });

    it("draws the map with the sites that have a position, beside the same list", async () => {
      renderMap();
      expect(await screen.findByTestId("sites-map-stub")).toBeDefined();
      expect(sitesMapProps.mock.calls.at(-1)?.[0]).toMatchObject({
        apiKey: "test-key", // pragma: allowlist secret — a test fixture, not a Maps key
        sites: [expect.objectContaining({ id: "wh-1" }), expect.objectContaining({ id: "wh-2" })],
        selectedId: null,
      });
      expect(screen.getByRole("table", { name: "Warehouses with a location" })).toBeDefined();
      expect(screen.queryByText("The map isn't set up here, so your warehouses are listed instead")).toBeNull();
    });

    it("selects a site from the map and from the list's Show on map, and opens floors from the popup", async () => {
      const user = userEvent.setup();
      const { where } = renderMap();
      await screen.findByTestId("sites-map-stub");

      await user.click(screen.getByRole("button", { name: "pin Pune Hub" }));
      expect(screen.getByText("map selected: wh-2")).toBeDefined();

      await user.click(screen.getByRole("button", { name: "Show on map Bhiwandi DC" }));
      expect(screen.getByText("map selected: wh-1")).toBeDefined();

      await user.click(screen.getByRole("button", { name: "popup open floors" }));
      await waitFor(() => expect(where().pathname).toBe("/warehouses/wh-1/floors"));
    });

    it("opens with the warehouse from ?focus= selected", async () => {
      renderMap([READ], "/warehouses/map?focus=wh-2");
      expect(await screen.findByText("map selected: wh-2")).toBeDefined();
    });

    it("falls back to the list with a notice when Google refuses the key, and the list keeps working", async () => {
      const user = userEvent.setup();
      renderMap();
      await user.click(await screen.findByRole("button", { name: "google refuses the key" }));

      expect(await screen.findByText("The map couldn't load, so your warehouses are listed instead")).toBeDefined();
      expect(screen.queryByTestId("sites-map-stub")).toBeNull();
      expect(screen.getByRole("table", { name: "Warehouses with a location" })).toBeDefined();
      expect(screen.getByRole("button", { name: "Open floors Pune Hub" })).toBeDefined();
      expect(console.warn).toHaveBeenCalledWith("[operations] maps_load_failed (auth_failure)");
    });

    it("falls back the same way when the script fails to load, and reports it once per session", async () => {
      const user = userEvent.setup();
      renderMap();
      await user.click(await screen.findByRole("button", { name: "script fails" }));
      expect(await screen.findByText("The map couldn't load, so your warehouses are listed instead")).toBeDefined();

      await user.click(screen.getByRole("button", { name: "Try the map again" }));
      await user.click(await screen.findByRole("button", { name: "script fails" }));
      expect(console.warn).toHaveBeenCalledTimes(1);
    });

    it("lets the user try the map again after a failure", async () => {
      const user = userEvent.setup();
      renderMap();
      await user.click(await screen.findByRole("button", { name: "google refuses the key" }));
      await user.click(await screen.findByRole("button", { name: "Try the map again" }));
      expect(await screen.findByTestId("sites-map-stub")).toBeDefined();
      expect(screen.queryByText("The map couldn't load, so your warehouses are listed instead")).toBeNull();
    });

    it("falls back to the list when the map throws while rendering, without breaking the page", async () => {
      vi.spyOn(console, "error").mockImplementation(() => undefined);
      sitesMapThrows = true;
      renderMap();
      expect(await screen.findByText("The map couldn't load, so your warehouses are listed instead")).toBeDefined();
      expect(screen.getByRole("table", { name: "Warehouses with a location" })).toBeDefined();
      expect(screen.getByRole("heading", { name: "Warehouse map" })).toBeDefined();
    });

    it("says no warehouse has a location yet, rather than drawing an empty map", async () => {
      getWarehousesMap.mockResolvedValue(data({ sites: [] }));
      renderMap();
      expect(await screen.findByText("No warehouse has a location yet")).toBeDefined();
      expect(screen.queryByTestId("sites-map-stub")).toBeNull();
      expect(screen.getByRole("table", { name: "Warehouses without a location" })).toBeDefined();
    });

    it("leaves a site with an impossible position off the map and lists it among the unlocated", async () => {
      getWarehousesMap.mockResolvedValue(
        data({ sites: [SITE_A, { ...SITE_B, lat_micro: 99_000_000 }], without_coordinates: [] }),
      );
      renderMap();
      await screen.findByTestId("sites-map-stub");
      expect((sitesMapProps.mock.calls.at(-1)?.[0] as { sites: unknown[] }).sites).toHaveLength(1);
      const unlocated = screen.getByRole("table", { name: "Warehouses without a location" });
      expect(unlocated.textContent).toContain("Pune Hub");
    });
  });

  describe("warehouses without a position", () => {
    beforeEach(() => {
      vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "");
    });

    it("lists them apart, with Set location for someone who can edit warehouses", async () => {
      renderMap([READ, WRITE]);
      const table = await screen.findByRole("table", { name: "Warehouses without a location" });
      expect(table.textContent).toContain("Goa Store");
      expect(screen.getByRole("button", { name: "Set location" })).toBeDefined();
    });

    it("tells someone who cannot edit to ask an administrator, instead of offering a refused control", async () => {
      renderMap([READ]);
      await screen.findByRole("table", { name: "Warehouses without a location" });
      expect(screen.queryByRole("button", { name: "Set location" })).toBeNull();
      expect(screen.getByText("Ask an administrator")).toBeDefined();
    });

    it("opens the warehouse dialog, saves the position as micro-degrees, and reloads the map data", async () => {
      const user = userEvent.setup();
      updateWarehouse.mockResolvedValue({ ...UNLOCATED, lat_micro: 15_299_600, lng_micro: 73_957_800 });
      renderMap([READ, WRITE]);
      await user.click(await screen.findByRole("button", { name: "Set location" }));

      await user.type(screen.getByLabelText(/Latitude/), "15.2996");
      await user.type(screen.getByLabelText(/Longitude/), "73.9578");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await waitFor(() =>
        expect(updateWarehouse).toHaveBeenCalledWith("wh-3", {
          name: "Goa Store",
          location: "Margao",
          lat_micro: 15_299_600,
          lng_micro: 73_957_800,
        }),
      );
      await waitFor(() => expect(getWarehousesMap).toHaveBeenCalledTimes(2));
    });
  });

  describe("loading, failing and empty", () => {
    beforeEach(() => {
      vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "");
    });

    it("holds the page's place with a skeleton, and says so after 8 seconds", async () => {
      vi.useFakeTimers();
      getWarehousesMap.mockReturnValue(new Promise(() => undefined));
      renderMap();
      await act(async () => {
        await Promise.resolve();
      });
      expect(document.querySelector("[aria-busy='true']")).not.toBeNull();
      expect(screen.getByRole("status").textContent).toBe("");
      await act(async () => {
        vi.advanceTimersByTime(8000);
      });
      expect(screen.getByRole("status").textContent).toContain("taking longer than usual");
    });

    it("shows the failure with a retry that asks again", async () => {
      const user = userEvent.setup();
      getWarehousesMap.mockRejectedValueOnce(new ApiError(500, "The warehouse service is down", { detail: "The warehouse service is down" }));
      renderMap();
      expect(await screen.findByText("The warehouse service is down")).toBeDefined();
      await user.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByRole("table", { name: "Warehouses with a location" })).toBeDefined();
    });

    it("explains an organization with no warehouses at all and sends them to add one", async () => {
      const user = userEvent.setup();
      getWarehousesMap.mockResolvedValue({ sites: [], without_coordinates: [] });
      const { where } = renderMap();
      expect(await screen.findByText("No warehouses yet")).toBeDefined();
      await user.click(screen.getByRole("button", { name: "Go to warehouses" }));
      await waitFor(() => expect(where().pathname).toBe("/warehouses"));
    });
  });

  describe("on a phone", () => {
    it("turns the lists into cards rather than sideways-scrolling tables", async () => {
      vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "");
      stubViewport(375);
      renderMap([READ, WRITE]);
      expect(await screen.findByText("Bhiwandi DC")).toBeDefined();
      expect(screen.queryByRole("table")).toBeNull();
      expect(screen.getByRole("button", { name: "Open floors Bhiwandi DC" })).toBeDefined();
      expect(screen.getByRole("button", { name: "Set location" })).toBeDefined();
    });
  });
});
