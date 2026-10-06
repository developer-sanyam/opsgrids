/**
 * The floor layout editor (Fulfilment plan §2/F7). Real pointer-drag canvas interaction is not
 * exercised here — jsdom has no `getScreenCTM`, so the coordinate math a real browser drag needs
 * cannot run in this environment — but every button-driven part of the flow the acceptance
 * scenario cares about is: opening a draft, placing a location (auto-creating its walk node),
 * saving, validating, publishing, and rolling back from the version history.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";

import type {
  LayoutGeometryIn,
  LayoutGeometryOut,
  LayoutValidationOut,
  LayoutVersionOut,
  LocationKind,
  LocationOut,
  PublishedLayoutOut,
  WarehouseFloorOut,
} from "@/api/types";
import { overlappingPlacements, validateOverlaps } from "@/test-utils/floorOverlap";
import {
  installCanvasPointerSupport,
  stubCoarsePointer,
  stubElementSize,
  uninstallCanvasPointerSupport,
} from "@/test-utils/pointer";
import { FloorLayoutEditorPage } from "./FloorLayoutEditor";

const getWarehouseFloor = vi.fn();
const listWarehouseFloors = vi.fn();
const getPublishedLayout = vi.fn();
const listLocations = vi.fn();
const listLayoutVersions = vi.fn();
const getLayoutGeometry = vi.fn();
const createLayoutVersion = vi.fn();
const saveLayoutGeometry = vi.fn();
const validateLayoutVersion = vi.fn();
const publishLayoutVersion = vi.fn();
const discardLayoutVersion = vi.fn();
const computeWarehouseRoute = vi.fn();

vi.mock("@/api/client", () => ({
  getWarehouseFloor: (...args: unknown[]) => getWarehouseFloor(...args),
  listWarehouseFloors: (...args: unknown[]) => listWarehouseFloors(...args),
  getPublishedLayout: (...args: unknown[]) => getPublishedLayout(...args),
  listLocations: (...args: unknown[]) => listLocations(...args),
  listLayoutVersions: (...args: unknown[]) => listLayoutVersions(...args),
  getLayoutGeometry: (...args: unknown[]) => getLayoutGeometry(...args),
  createLayoutVersion: (...args: unknown[]) => createLayoutVersion(...args),
  saveLayoutGeometry: (...args: unknown[]) => saveLayoutGeometry(...args),
  validateLayoutVersion: (...args: unknown[]) => validateLayoutVersion(...args),
  publishLayoutVersion: (...args: unknown[]) => publishLayoutVersion(...args),
  discardLayoutVersion: (...args: unknown[]) => discardLayoutVersion(...args),
  computeWarehouseRoute: (...args: unknown[]) => computeWarehouseRoute(...args),
}));

const USER: MeOut = {
  user_id: "user-1",
  email: "ops@example.com",
  full_name: "Asha Rao",
  organization_id: "org-1",
  membership_id: "mem-1",
  roles: [],
  amr: [],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [],
};

const AUTH_VALUE: AuthContextValue = {
  status: "authenticated",
  user: USER,
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

const FLOOR: WarehouseFloorOut = { id: "floor-1", warehouse_id: "wh-1", floor_number: 1, name: "Ground" };
const OTHER_FLOOR: WarehouseFloorOut = { id: "floor-0", warehouse_id: "wh-1", floor_number: 0, name: "Mezzanine" };

/** What `getPublishedLayout` answers for `floor` when `locations` are placed on its published layout. */
function publishedLayoutOf(floor: WarehouseFloorOut, locations: LocationOut[]): PublishedLayoutOut {
  return {
    floor_id: floor.id,
    warehouse_id: floor.warehouse_id,
    floor_name: floor.name,
    published: locations.length > 0,
    layout_version_id: locations.length > 0 ? `v-pub-${floor.id}` : null,
    placements: locations.map((l, i) => ({
      location_id: l.id,
      code: l.code,
      name: l.name,
      kind: l.kind,
      parent_id: l.parent_id,
      x_mm: i * 1200,
      y_mm: 0,
      z_mm: null,
      width_mm: 1000,
      depth_mm: 600,
      height_mm: null,
    })),
  };
}

const LOCATION_A: LocationOut = {
  id: "loc-a",
  warehouse_id: "wh-1",
  parent_id: null,
  kind: "bin",
  code: "LOC-A",
  name: "Location A",
  is_default: false,
  barcode: null,
  pick_sequence: null,
  max_weight_grams: null,
  max_volume_milli: null,
  storage_type: null,
};

const EMPTY_GEOMETRY = (version: LayoutVersionOut): LayoutGeometryOut => ({
  version,
  placements: [],
  walk_nodes: [],
  walk_edges: [],
});

const VALID: LayoutValidationOut = { is_valid: true, issues: [] };

const WRITE_PERM = "operations.warehouse.layout.write:ORG";
const PUBLISH_PERM = "operations.warehouse.layout.publish:ORG";

function renderPage(grants: string[] = []) {
  const router = createMemoryRouter(
    [{ path: "/warehouses/:id/floors/:floorId", element: <FloorLayoutEditorPage /> }],
    { initialEntries: ["/warehouses/wh-1/floors/floor-1"] },
  );
  render(
    <AuthContext.Provider value={AUTH_VALUE}>
      <PermissionProvider grants={grants} entitlements={[]}>
        <RouterProvider router={router} />
      </PermissionProvider>
    </AuthContext.Provider>,
  );
  return router;
}

describe("FloorLayoutEditorPage", () => {
  beforeEach(() => {
    getWarehouseFloor.mockReset();
    listWarehouseFloors.mockReset();
    getPublishedLayout.mockReset();
    listLocations.mockReset();
    listLayoutVersions.mockReset();
    getLayoutGeometry.mockReset();
    createLayoutVersion.mockReset();
    saveLayoutGeometry.mockReset();
    validateLayoutVersion.mockReset();
    publishLayoutVersion.mockReset();
    discardLayoutVersion.mockReset();
    computeWarehouseRoute.mockReset();

    getWarehouseFloor.mockResolvedValue(FLOOR);
    // A warehouse of one floor — this one — unless a test adds another.
    listWarehouseFloors.mockResolvedValue([FLOOR]);
    getPublishedLayout.mockResolvedValue(publishedLayoutOf(OTHER_FLOOR, []));
    listLocations.mockResolvedValue([LOCATION_A]);
    validateLayoutVersion.mockResolvedValue(VALID);
  });

  it("offers to start editing when the floor has no layout at all", async () => {
    listLayoutVersions.mockResolvedValue([]);
    renderPage([WRITE_PERM]);

    expect(await screen.findByText("No layout yet")).toBeDefined();
    expect(screen.getByRole("button", { name: "Start editing" })).toBeDefined();
  });

  it("does not offer to start editing without layout.write", async () => {
    listLayoutVersions.mockResolvedValue([]);
    renderPage([]);

    await screen.findByText("No layout yet");
    expect(screen.queryByRole("button", { name: "Start editing" })).toBeNull();
  });

  it("shows the published layout read-only when there is no draft", async () => {
    const published: LayoutVersionOut = {
      id: "v-pub",
      warehouse_floor_id: "floor-1",
      version_number: 1,
      status: "published",
      label: "",
      published_at: "2026-09-01T00:00:00Z",
      published_by: "user-1",
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    };
    listLayoutVersions.mockResolvedValue([published]);
    getLayoutGeometry.mockResolvedValue(EMPTY_GEOMETRY(published));
    renderPage([WRITE_PERM]);

    expect(await screen.findByText("Viewing the published layout — read-only")).toBeDefined();
    // No editing toolbar (mode buttons) in read-only mode.
    expect(screen.queryByRole("button", { name: "Select / move" })).toBeNull();
  });

  it("opens a new draft from the empty state", async () => {
    const user = userEvent.setup();
    listLayoutVersions.mockResolvedValueOnce([]);
    const draft: LayoutVersionOut = {
      id: "v-draft",
      warehouse_floor_id: "floor-1",
      version_number: 1,
      status: "draft",
      label: "",
      published_at: null,
      published_by: null,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    };
    createLayoutVersion.mockResolvedValue(draft);
    listLayoutVersions.mockResolvedValue([draft]);
    getLayoutGeometry.mockResolvedValue(EMPTY_GEOMETRY(draft));

    renderPage([WRITE_PERM]);
    await user.click(await screen.findByRole("button", { name: "Start editing" }));

    await waitFor(() => {
      expect(createLayoutVersion).toHaveBeenCalledWith("floor-1", { clone_from_published: true });
    });
    expect(await screen.findByText("Editing draft")).toBeDefined();
  });

  describe("with an open draft", () => {
    const draft: LayoutVersionOut = {
      id: "v-draft",
      warehouse_floor_id: "floor-1",
      version_number: 1,
      status: "draft",
      label: "",
      published_at: null,
      published_by: null,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    };

    beforeEach(() => {
      listLayoutVersions.mockResolvedValue([draft]);
      getLayoutGeometry.mockResolvedValue(EMPTY_GEOMETRY(draft));
    });

    it("lists the unplaced location and places it on the floor", async () => {
      const user = userEvent.setup();
      renderPage([WRITE_PERM]);

      await screen.findByText("Editing draft");
      expect(screen.getByText("LOC-A")).toBeDefined();
      await user.click(screen.getByRole("button", { name: "Place" }));

      // Placed: no longer offered as unplaced, and Save draft is now enabled.
      await waitFor(() => {
        expect(screen.getByText("Every location in this warehouse is on the floor.")).toBeDefined();
      });
      expect(screen.getByRole("button", { name: "Save draft" })).not.toHaveProperty("disabled", true);
    });

    it("saves the placed location and its auto-created walk node", async () => {
      const user = userEvent.setup();
      const saved: LayoutGeometryOut = {
        version: draft,
        placements: [
          {
            location_id: "loc-a",
            x_mm: 0,
            y_mm: 0,
            z_mm: null,
            width_mm: 1000,
            depth_mm: 1000,
            height_mm: null,
            location_code: "LOC-A",
            location_kind: "bin",
          },
        ],
        walk_nodes: [{ id: "n1", code: "L-LOCA", location_id: "loc-a", x_mm: 500, y_mm: 500, z_mm: null }],
        walk_edges: [],
      };
      saveLayoutGeometry.mockResolvedValue(saved);
      renderPage([WRITE_PERM]);

      await screen.findByText("Editing draft");
      await user.click(screen.getByRole("button", { name: "Place" }));
      await user.click(screen.getByRole("button", { name: "Save draft" }));

      await waitFor(() => expect(saveLayoutGeometry).toHaveBeenCalledTimes(1));
      const [versionId, payload] = saveLayoutGeometry.mock.calls[0] as [string, { placements: unknown[]; walk_nodes: unknown[] }];
      expect(versionId).toBe("v-draft");
      expect(payload.placements).toHaveLength(1);
      expect(payload.walk_nodes).toHaveLength(1);
      // Validation re-runs after a save.
      await waitFor(() => expect(validateLayoutVersion).toHaveBeenCalledWith("v-draft"));
    });

    it("keeps Publish disabled until validation says the layout is clean, then publishes on confirm", async () => {
      const user = userEvent.setup();
      renderPage([WRITE_PERM, PUBLISH_PERM]);
      await screen.findByText("Editing draft");

      const publishButton = screen.getByRole("button", { name: "Publish" });
      expect(publishButton).toHaveProperty("disabled", true);
      await user.click(screen.getByRole("button", { name: "Check for problems" }));
      await waitFor(() => expect(validateLayoutVersion).toHaveBeenCalledWith("v-draft"));
      await waitFor(() => expect(publishButton).toHaveProperty("disabled", false));

      publishLayoutVersion.mockResolvedValue({ ...draft, status: "published" });
      await user.click(publishButton);
      const confirmButtons = await screen.findAllByRole("button", { name: "Publish" });
      const confirmButton = confirmButtons.find((b) => b !== publishButton);
      if (!confirmButton) throw new Error("expected a second Publish button, in the confirm dialog");
      await user.click(confirmButton);

      await waitFor(() => expect(publishLayoutVersion).toHaveBeenCalledWith("v-draft"));
    });

    it("discards the draft on confirm", async () => {
      const user = userEvent.setup();
      discardLayoutVersion.mockResolvedValue(undefined);
      renderPage([WRITE_PERM]);
      await screen.findByText("Editing draft");

      const discardButton = screen.getByRole("button", { name: "Discard draft" });
      await user.click(discardButton);
      const confirmButtons = await screen.findAllByRole("button", { name: "Discard draft" });
      const confirmButton = confirmButtons.find((b) => b !== discardButton);
      if (!confirmButton) throw new Error("expected a second Discard draft button, in the confirm dialog");
      await user.click(confirmButton);

      await waitFor(() => expect(discardLayoutVersion).toHaveBeenCalledWith("v-draft"));
    });

    it("shows validation problems reported by the server", async () => {
      const user = userEvent.setup();
      validateLayoutVersion.mockResolvedValue({
        is_valid: false,
        issues: [{ kind: "overlap", message: "Locations LOC-A and LOC-B overlap", location_ids: ["loc-a", "loc-b"], node_id: null }],
      });
      renderPage([WRITE_PERM, PUBLISH_PERM]);
      await screen.findByText("Editing draft");

      await user.click(screen.getByRole("button", { name: "Check for problems" }));
      expect(await screen.findByText("Locations LOC-A and LOC-B overlap")).toBeDefined();
      expect(screen.getByRole("button", { name: "Publish" })).toHaveProperty("disabled", true);
    });
  });

  describe("showing the floor's locations on the canvas", () => {
    const draft: LayoutVersionOut = {
      id: "v-draft",
      warehouse_floor_id: "floor-1",
      version_number: 1,
      status: "draft",
      label: "",
      published_at: null,
      published_by: null,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    };

    function loc(id: string, kind: LocationKind, code: string, parentId: string | null = null): LocationOut {
      return { ...LOCATION_A, id, kind, code, name: code, parent_id: parentId };
    }

    /** The owner's floor 0: two zones, a rack, two shelves, two bins — all of it unplaced. */
    const FLOOR_ZERO: LocationOut[] = [
      loc("z1", "zone", "Z1"),
      loc("z2", "zone", "Z2"),
      loc("r1", "rack", "Z1-R1", "z1"),
      loc("s1", "shelf", "Z1-R1-S1", "r1"),
      loc("b1", "bin", "Z1-R1-S1-B1", "s1"),
      loc("b2", "bin", "Z1-R1-S1-B2", "s1"),
      loc("s2", "shelf", "Z1-R1-S2", "r1"),
    ];

    /** What the server last saved — the validate mock reads it, so "Check for problems" answers
     *  with the real overlap rule about the layout the editor actually sent. */
    let saved: LayoutGeometryIn;

    function geometryOf(payload: LayoutGeometryIn, locations: LocationOut[] = FLOOR_ZERO): LayoutGeometryOut {
      return {
        version: draft,
        placements: payload.placements.map((p) => {
          const location = locations.find((l) => l.id === p.location_id);
          return {
            location_id: p.location_id,
            x_mm: p.x_mm,
            y_mm: p.y_mm,
            z_mm: p.z_mm ?? null,
            width_mm: p.width_mm ?? 1000,
            depth_mm: p.depth_mm ?? 1000,
            height_mm: p.height_mm ?? null,
            location_code: location?.code ?? p.location_id,
            location_kind: location?.kind ?? "bin",
          };
        }),
        walk_nodes: payload.walk_nodes.map((n, i) => ({
          id: `n${i}`,
          code: n.code,
          location_id: n.location_id ?? null,
          x_mm: n.x_mm,
          y_mm: n.y_mm,
          z_mm: n.z_mm ?? null,
        })),
        walk_edges: [],
      };
    }

    function loadDraft(geometry: LayoutGeometryOut = EMPTY_GEOMETRY(draft)) {
      getLayoutGeometry.mockResolvedValue(geometry);
      saved = {
        placements: geometry.placements.map((p) => ({
          location_id: p.location_id,
          x_mm: p.x_mm,
          y_mm: p.y_mm,
          width_mm: p.width_mm,
          depth_mm: p.depth_mm,
        })),
        walk_nodes: [],
        walk_edges: [],
      };
    }

    const scrollIntoView = vi.fn();

    beforeEach(() => {
      scrollIntoView.mockReset();
      Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
      listLocations.mockResolvedValue(FLOOR_ZERO);
      listLayoutVersions.mockResolvedValue([draft]);
      saveLayoutGeometry.mockImplementation((_versionId: string, payload: LayoutGeometryIn) => {
        saved = payload;
        return Promise.resolve(geometryOf(payload));
      });
      validateLayoutVersion.mockImplementation(() => Promise.resolve(validateOverlaps(saved)));
      loadDraft();
    });

    afterEach(() => {
      Reflect.deleteProperty(Element.prototype, "scrollIntoView");
    });

    function canvas() {
      return screen.getByRole("img", { name: "Floor layout canvas" });
    }

    describe("the empty canvas", () => {
      it("says there is nothing on the floor, how many are waiting, and both ways forward", async () => {
        renderPage([WRITE_PERM]);

        expect(await screen.findByText("No locations on this floor yet")).toBeDefined();
        expect(screen.getByText(/unplaced locations in this warehouse/)).toBeDefined();
        expect(screen.getByRole("button", { name: "Auto-arrange all 7 unplaced locations" })).toBeDefined();
        expect(screen.getByRole("button", { name: "Place one at a time" })).toBeDefined();
        // And the list those unplaced locations are in, with its count in the header.
        expect(screen.getByRole("button", { name: "Unplaced locations 7" })).toBeDefined();
        expect(screen.getAllByRole("button", { name: "Place" })).toHaveLength(7);
      });

      it("says there are no locations in the warehouse, with a way to add them", async () => {
        listLocations.mockResolvedValue([]);
        renderPage([WRITE_PERM]);

        expect(await screen.findByText("No locations in this warehouse yet")).toBeDefined();
        expect(screen.getByRole("link", { name: "Go to locations" }).getAttribute("href")).toBe("/warehouses/wh-1/locations");
        expect(screen.queryByRole("button", { name: /Auto-arrange all/ })).toBeNull();
        // The toolbar's own action has nothing to act on.
        expect(screen.getByRole("button", { name: "Auto-arrange unplaced" })).toHaveProperty("disabled", true);
        expect(screen.getByText("This warehouse has no locations yet.")).toBeDefined();
      });

      it("does not say the floor is empty, or let anything be placed, while its layout is still loading", async () => {
        getLayoutGeometry.mockReturnValue(new Promise(() => undefined));
        renderPage([WRITE_PERM]);

        await waitFor(() => expect(getLayoutGeometry).toHaveBeenCalledWith("v-draft"));
        expect(screen.queryByText("No locations on this floor yet")).toBeNull();
        expect(screen.queryByRole("button", { name: /Auto-arrange/ })).toBeNull();
        expect(screen.queryByRole("button", { name: "Place" })).toBeNull();
        expect(screen.queryByText("Editing draft")).toBeNull();
      });

      it("shows a failed layout load as one, and offers no editor over a layout it could not read", async () => {
        getLayoutGeometry.mockRejectedValue(new Error("offline"));
        renderPage([WRITE_PERM]);

        expect(await screen.findByText("Couldn't load this layout")).toBeDefined();
        expect(screen.queryByText("No locations on this floor yet")).toBeNull();
        expect(screen.queryByRole("button", { name: "Save draft" })).toBeNull();
      });

      it("does not claim the warehouse is empty when its locations failed to load", async () => {
        listLocations.mockRejectedValue(new Error("offline"));
        renderPage([WRITE_PERM]);

        expect(await screen.findByText("Couldn't load this warehouse's locations")).toBeDefined();
        expect(screen.queryByText("No locations in this warehouse yet")).toBeNull();
      });

      it("offers no way forward to someone who cannot edit the layout", async () => {
        renderPage([]);

        expect(await screen.findByText("No locations on this floor yet")).toBeDefined();
        expect(screen.getByText(/Nothing is placed on this layout/)).toBeDefined();
        expect(screen.queryByRole("button", { name: /Auto-arrange/ })).toBeNull();
        expect(screen.queryByRole("button", { name: "Place one at a time" })).toBeNull();
        expect(screen.queryByRole("button", { name: /Unplaced locations/ })).toBeNull();
      });

      it("offers no way forward on a published layout, which is read-only", async () => {
        const published: LayoutVersionOut = { ...draft, id: "v-pub", status: "published", published_at: "2026-09-01T00:00:00Z" };
        listLayoutVersions.mockResolvedValue([published]);
        loadDraft(EMPTY_GEOMETRY(published));
        renderPage([WRITE_PERM, PUBLISH_PERM]);

        await screen.findByText("Viewing the published layout — read-only");
        expect(screen.getByText("No locations on this floor yet")).toBeDefined();
        expect(screen.queryByRole("button", { name: /Auto-arrange/ })).toBeNull();
        expect(screen.queryByRole("button", { name: "Place one at a time" })).toBeNull();
        expect(screen.queryByRole("switch", { name: "Edit by touch" })).toBeNull();
      });

      it("gives a draft's viewer without layout.write no Auto-arrange control", async () => {
        // A draft is open, so the toolbar shows, but its actions belong to layout.write.
        renderPage([PUBLISH_PERM]);

        await screen.findByText("Editing draft");
        expect(screen.queryByRole("button", { name: "Auto-arrange unplaced" })).toBeNull();
        expect(screen.queryByRole("button", { name: /Auto-arrange all/ })).toBeNull();
      });
    });

    describe("Auto-arrange", () => {
      it("places every unplaced location in one go, announces it, and leaves the draft unsaved", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);

        expect(screen.queryByText("Arranged 7 locations in a grid.", { exact: false })).toBeNull();
        await user.click(await screen.findByRole("button", { name: "Auto-arrange all 7 unplaced locations" }));

        // Said once, to assistive technology.
        expect(await screen.findByText("Placed 7 locations")).toBeDefined();
        // Drawn: every code is a label on the canvas, and the empty state is gone.
        const labels = Array.from(canvas().querySelectorAll("text")).map((t) => t.textContent);
        expect(labels.sort()).toEqual(FLOOR_ZERO.map((l) => l.code).sort());
        expect(screen.queryByText("No locations on this floor yet")).toBeNull();
        // Nothing left to place; the draft is dirty but nothing was saved yet.
        expect(screen.getByRole("button", { name: "Unplaced locations 0" })).toBeDefined();
        expect(screen.getByText("Every location in this warehouse is on the floor.")).toBeDefined();
        expect(screen.getByRole("button", { name: "Save draft" })).toHaveProperty("disabled", false);
        expect(saveLayoutGeometry).not.toHaveBeenCalled();
        // And says what it did, and that it can be taken back.
        expect(screen.getByText(/Arranged 7 locations in a grid\./)).toBeDefined();
        expect(screen.getByRole("button", { name: "Undo" })).toBeDefined();
        // Focus is on the canvas, not lost with the button that was clicked.
        expect(screen.getByRole("group", { name: "Floor layout" })).toBe(document.activeElement);
      });

      it("is also on the toolbar, secondary, and disabled once nothing is left to place", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);
        const toolbarButton = await screen.findByRole("button", { name: "Auto-arrange unplaced" });
        expect(toolbarButton).toHaveProperty("disabled", false);

        await user.click(toolbarButton);

        await waitFor(() => expect(screen.getByRole("button", { name: "Auto-arrange unplaced" })).toHaveProperty("disabled", true));
        expect(await screen.findByText("Placed 7 locations")).toBeDefined();
      });

      it("saves a layout the server's overlap rule accepts, and Check for problems passes", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM, PUBLISH_PERM]);
        await user.click(await screen.findByRole("button", { name: "Auto-arrange all 7 unplaced locations" }));
        await user.click(screen.getByRole("button", { name: "Save draft" }));

        await waitFor(() => expect(saveLayoutGeometry).toHaveBeenCalledTimes(1));
        const [, payload] = saveLayoutGeometry.mock.calls[0] as [string, LayoutGeometryIn];
        expect(payload.placements).toHaveLength(7);
        // Footprints only: a walk node per location, with no edges, would fail the connectivity rule.
        expect(payload.walk_nodes).toEqual([]);
        expect(overlappingPlacements(payload.placements)).toEqual([]);
        for (const p of payload.placements) {
          expect(p.x_mm).toBeGreaterThanOrEqual(0);
          expect(p.y_mm).toBeGreaterThanOrEqual(0);
          expect(p.width_mm).toBeGreaterThan(0);
          expect(p.depth_mm).toBeGreaterThan(0);
        }

        // The save re-runs validation; "Check for problems" agrees with it.
        await waitFor(() => expect(validateLayoutVersion).toHaveBeenCalledWith("v-draft"));
        await user.click(screen.getByRole("button", { name: "Check for problems" }));
        await waitFor(() => expect(validateLayoutVersion).toHaveBeenCalledTimes(2));
        expect(screen.queryByText(/problem\(s\) found/)).toBeNull();
        await waitFor(() => expect(screen.getByRole("button", { name: "Publish" })).toHaveProperty("disabled", false));
      });

      it("keeps Publish disabled while the arrangement is unsaved, even after a clean check", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM, PUBLISH_PERM]);
        await screen.findByText("Editing draft");

        await user.click(screen.getByRole("button", { name: "Check for problems" }));
        await waitFor(() => expect(screen.getByRole("button", { name: "Publish" })).toHaveProperty("disabled", false));

        await user.click(screen.getByRole("button", { name: "Auto-arrange unplaced" }));
        expect(screen.getByRole("button", { name: "Publish" })).toHaveProperty("disabled", true);
        expect(screen.getByText(/Unsaved changes/)).toBeDefined();

        await user.click(screen.getByRole("button", { name: "Save draft" }));
        await waitFor(() => expect(screen.getByRole("button", { name: "Publish" })).toHaveProperty("disabled", false));
      });

      it("still reports what the server finds, and keeps Publish disabled", async () => {
        const user = userEvent.setup();
        validateLayoutVersion.mockResolvedValue({
          is_valid: false,
          issues: [{ kind: "overlap", message: "Locations z1 and z2 overlap on the floor plan", location_ids: ["z1", "z2"], node_id: null }],
        });
        renderPage([WRITE_PERM, PUBLISH_PERM]);
        await user.click(await screen.findByRole("button", { name: "Auto-arrange all 7 unplaced locations" }));
        await user.click(screen.getByRole("button", { name: "Save draft" }));

        expect(await screen.findByText("Locations z1 and z2 overlap on the floor plan")).toBeDefined();
        expect(screen.getByRole("button", { name: "Publish" })).toHaveProperty("disabled", true);
      });

      it("takes the arrangement back on Undo", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);
        await user.click(await screen.findByRole("button", { name: "Auto-arrange all 7 unplaced locations" }));
        await user.click(screen.getByRole("button", { name: "Undo" }));

        expect(await screen.findByText("No locations on this floor yet")).toBeDefined();
        expect(screen.getByRole("button", { name: "Unplaced locations 7" })).toBeDefined();
        expect(screen.getByRole("button", { name: "Save draft" })).toHaveProperty("disabled", true);
        expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
      });

      it("arranges beneath what is already on the floor, without touching it", async () => {
        const user = userEvent.setup();
        loadDraft(
          geometryOf({
            placements: [{ location_id: "z1", x_mm: 0, y_mm: 0, width_mm: 4000, depth_mm: 800 }],
            walk_nodes: [],
            walk_edges: [],
          }),
        );
        renderPage([WRITE_PERM]);
        await user.click(await screen.findByRole("button", { name: "Auto-arrange unplaced" }));
        await user.click(screen.getByRole("button", { name: "Save draft" }));

        await waitFor(() => expect(saveLayoutGeometry).toHaveBeenCalledTimes(1));
        const [, payload] = saveLayoutGeometry.mock.calls[0] as [string, LayoutGeometryIn];
        expect(payload.placements).toHaveLength(7);
        expect(payload.placements.find((p) => p.location_id === "z1")).toMatchObject({ x_mm: 0, y_mm: 0, width_mm: 4000, depth_mm: 800 });
        expect(overlappingPlacements(payload.placements)).toEqual([]);
        for (const p of payload.placements.filter((q) => q.location_id !== "z1")) {
          expect(p.y_mm).toBeGreaterThanOrEqual(800);
        }
      });
    });

    describe("Place, one at a time", () => {
      it("draws the new rectangle on the canvas, selects it, announces it and brings that rectangle into view", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");

        const placeButtons = await screen.findAllByRole("button", { name: "Place" });
        await user.click(placeButtons[0] as HTMLElement);

        expect(await screen.findByText("Placed Z1")).toBeDefined();
        expect(Array.from(canvas().querySelectorAll("text")).some((t) => t.textContent === "Z1")).toBe(true);
        // Selected: its details are open, with a footprint of its own kind (a zone is a wide band).
        expect(screen.getByRole("heading", { name: "Z1" })).toBeDefined();
        expect((screen.getByLabelText("Width (mm)") as HTMLInputElement).value).toBe("4000");
        // The empty state has given way to the plan.
        expect(screen.queryByText("No locations on this floor yet")).toBeNull();
        // The new rectangle itself is what is scrolled to, so a zoomed plan follows it.
        await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith(expect.objectContaining({ block: "nearest", inline: "nearest" })));
        const scrolled = scrollIntoView.mock.contexts[0] as Element;
        expect(scrolled.getAttribute("data-placement-id")).toBe("z1");
      });

      it("leaves keyboard focus in the list, on the next Place button, so a run of them is one press each", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");

        const first = (await screen.findAllByRole("button", { name: "Place" }))[0] as HTMLElement;
        first.focus();
        await user.keyboard("{Enter}");

        expect(await screen.findByText("Placed Z1")).toBeDefined();
        // Not on the canvas, and not on the placed rectangle's four fields and Remove button: on the
        // very next location waiting in the list.
        const next = document.activeElement as HTMLElement;
        expect(next.textContent).toBe("Place");
        expect(next.getAttribute("data-place-for")).toBe("z2");

        // And the run carries on without a Tab: Enter again places Z2, focus moves to the one after.
        await user.keyboard("{Enter}");
        expect(await screen.findByText("Placed Z2")).toBeDefined();
        expect((document.activeElement as HTMLElement).getAttribute("data-place-for")).toBe("r1");
      });

      it("hands focus to the list's header when the last one is placed", async () => {
        const user = userEvent.setup();
        listLocations.mockResolvedValue([loc("only", "bin", "ONLY")]);
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");

        await user.click(await screen.findByRole("button", { name: "Place" }));

        await screen.findByText("Placed ONLY");
        expect(document.activeElement).toBe(screen.getByRole("button", { name: "Unplaced locations 0" }));
        expect(screen.getByText("Every location in this warehouse is on the floor.")).toBeDefined();
      });

      it("puts each next one in free space: a placed set saves with no overlap and a walk node each", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");

        // Place all seven, one click at a time, always the first still listed.
        for (let placed = 0; placed < FLOOR_ZERO.length; placed += 1) {
          await user.click((await screen.findAllByRole("button", { name: "Place" }))[0] as HTMLElement);
          await waitFor(() => expect(screen.queryAllByRole("button", { name: "Place" })).toHaveLength(FLOOR_ZERO.length - placed - 1));
        }
        await user.click(screen.getByRole("button", { name: "Save draft" }));

        await waitFor(() => expect(saveLayoutGeometry).toHaveBeenCalledTimes(1));
        const [, payload] = saveLayoutGeometry.mock.calls[0] as [string, LayoutGeometryIn];
        expect(payload.placements).toHaveLength(7);
        expect(payload.walk_nodes).toHaveLength(7);
        expect(overlappingPlacements(payload.placements)).toEqual([]);
      });
    });

    describe("the unplaced list", () => {
      it("folds away from its header, and 'Place one at a time' opens it and moves focus there", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);
        const header = await screen.findByRole("button", { name: "Unplaced locations 7" });
        expect(header.getAttribute("aria-expanded")).toBe("true");

        await user.click(header);
        expect(header.getAttribute("aria-expanded")).toBe("false");
        expect(screen.queryAllByRole("button", { name: "Place" })).toHaveLength(0);

        await user.click(screen.getByRole("button", { name: "Place one at a time" }));
        expect(header.getAttribute("aria-expanded")).toBe("true");
        expect(screen.getAllByRole("button", { name: "Place" })).toHaveLength(7);
        expect(document.activeElement).toBe(header);
      });
    });

    describe("zoom", () => {
      it("has nothing to zoom or switch on an empty canvas", async () => {
        renderPage([WRITE_PERM]);
        await screen.findByText("No locations on this floor yet");

        expect(screen.queryByRole("button", { name: "Zoom in" })).toBeNull();
        expect(screen.queryByRole("switch", { name: "Edit by touch" })).toBeNull();
      });

      it("zooms by steps and goes back to the whole plan on Fit", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);
        await user.click(await screen.findByRole("button", { name: "Auto-arrange all 7 unplaced locations" }));

        expect(canvas().style.width).toBe("100%");
        expect(screen.getByRole("button", { name: "Zoom out" })).toHaveProperty("disabled", true);
        expect(screen.getByRole("button", { name: "Fit" })).toHaveProperty("disabled", true);

        await user.click(screen.getByRole("button", { name: "Zoom in" }));
        expect(canvas().style.width).toBe("150%");
        expect(screen.getByText("150%")).toBeDefined();

        await user.click(screen.getByRole("button", { name: "Zoom in" }));
        expect(canvas().style.width).toBe("200%");

        await user.click(screen.getByRole("button", { name: "Fit" }));
        expect(canvas().style.width).toBe("100%");
      });

      it("is a scroller a keyboard can reach once zoomed, and says so", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);
        await user.click(await screen.findByRole("button", { name: "Auto-arrange all 7 unplaced locations" }));

        // At Fit there is nothing to scroll, so it is not a tab stop (it can still be focused by script).
        const fitted = screen.getByRole("group", { name: "Floor layout" });
        expect(fitted.getAttribute("tabindex")).toBe("-1");

        await user.click(screen.getByRole("button", { name: "Zoom in" }));
        const zoomed = screen.getByRole("group", { name: "Floor layout, scrollable" });
        expect(zoomed.getAttribute("tabindex")).toBe("0");

        // And Tab does arrive on it, from the zoom controls, so the arrow keys can pan the plan.
        screen.getByRole("button", { name: "Fit" }).focus();
        for (let presses = 0; presses < 12 && document.activeElement !== zoomed; presses += 1) await user.tab();
        expect(document.activeElement).toBe(zoomed);

        await user.click(screen.getByRole("button", { name: "Fit" }));
        expect(screen.getByRole("group", { name: "Floor layout" }).getAttribute("tabindex")).toBe("-1");
      });

      it("goes back to the whole plan when Auto-arrange adds a grid beneath a zoomed one, and when it is undone", async () => {
        const user = userEvent.setup();
        loadDraft(
          geometryOf({
            placements: [{ location_id: "z1", x_mm: 0, y_mm: 0, width_mm: 4000, depth_mm: 800 }],
            walk_nodes: [],
            walk_edges: [],
          }),
        );
        renderPage([WRITE_PERM]);
        await user.click(await screen.findByRole("button", { name: "Zoom in" }));
        await user.click(screen.getByRole("button", { name: "Zoom in" }));
        expect(canvas().style.width).toBe("200%");

        await user.click(screen.getByRole("button", { name: "Auto-arrange unplaced" }));
        expect(canvas().style.width).toBe("100%");

        await user.click(screen.getByRole("button", { name: "Zoom in" }));
        await user.click(screen.getByRole("button", { name: "Undo" }));
        expect(canvas().style.width).toBe("100%");
      });
    });

    describe("with a finger or a pen", () => {
      /** Z1 above Z2, 4 m wide bands, and the two walk nodes J-1 and J-2 between them. */
      function planWithTwoShapes() {
        loadDraft(
          geometryOf({
            placements: [
              { location_id: "z1", x_mm: 0, y_mm: 0, width_mm: 4000, depth_mm: 800 },
              { location_id: "z2", x_mm: 0, y_mm: 2000, width_mm: 4000, depth_mm: 800 },
            ],
            walk_nodes: [
              { code: "J-1", location_id: null, x_mm: 500, y_mm: 1400, z_mm: null },
              { code: "J-2", location_id: null, x_mm: 3000, y_mm: 1400, z_mm: null },
            ],
            walk_edges: [],
          }),
        );
      }

      function shape(locationId: string): Element {
        const found = canvas().querySelector(`[data-placement-id="${locationId}"]`);
        if (!found) throw new Error(`no shape for ${locationId}`);
        return found;
      }

      const nodeDots = () => Array.from(canvas().querySelectorAll("circle"));
      const at = (kind: string, x: number, y: number) => ({ pointerType: kind, pointerId: 7, clientX: x, clientY: y });
      const selected = (code: string) => screen.queryByRole("heading", { name: code });

      function tap(target: Element, x: number, y: number, kind = "touch") {
        fireEvent.pointerDown(target, at(kind, x, y));
        fireEvent.pointerUp(target, at(kind, x, y));
      }

      beforeEach(() => installCanvasPointerSupport());
      afterEach(() => {
        vi.unstubAllGlobals();
        uninstallCanvasPointerSupport();
      });

      it.each(["touch", "pen"])("selects what a %s taps, and nothing it only pans across", async (kind) => {
        planWithTwoShapes();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");

        // Down alone is not yet a tap: the browser may be about to pan.
        fireEvent.pointerDown(shape("z1"), at(kind, 100, 100));
        expect(selected("Z1")).toBeNull();
        fireEvent.pointerUp(shape("z1"), at(kind, 100, 100));
        expect(selected("Z1")).not.toBeNull();

        // A pan the browser takes over ends in a cancel: no selection, and the shape stays put.
        fireEvent.pointerDown(shape("z2"), at(kind, 100, 2100));
        fireEvent.pointerMove(shape("z2"), at(kind, 100, 2300));
        fireEvent.pointerCancel(shape("z2"), at(kind, 100, 2300));
        expect(selected("Z2")).toBeNull();
        expect(shape("z2").getAttribute("y")).toBe("2000");

        // And a touch that moved before it lifted was not a tap either.
        fireEvent.pointerDown(shape("z2"), at(kind, 100, 2100));
        fireEvent.pointerUp(shape("z2"), at(kind, 160, 2100));
        expect(selected("Z2")).toBeNull();
      });

      it("never drags a shape with Edit by touch off — the finger pans, even over the selected one", async () => {
        planWithTwoShapes();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");
        tap(shape("z1"), 100, 100);
        expect(selected("Z1")).not.toBeNull();

        fireEvent.pointerDown(shape("z1"), at("touch", 100, 100));
        fireEvent.pointerMove(shape("z1"), at("touch", 700, 100));
        fireEvent.pointerUp(shape("z1"), at("touch", 700, 100));

        expect(shape("z1").getAttribute("x")).toBe("0");
        expect(canvas().getAttribute("class")).toContain("touch-auto");
        expect(shape("z1").getAttribute("class")).not.toContain("touch-none");
      });

      it("pans the plan by script for every drag that does not start on the selected shape, once Edit by touch holds the touch", async () => {
        const user = userEvent.setup();
        planWithTwoShapes();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");
        const region = screen.getByRole("group", { name: "Floor layout" });
        const pageScroller = document.documentElement;

        // Off, the browser pans, so the page does nothing of its own accord.
        expect(canvas().getAttribute("class")).toContain("touch-auto");
        fireEvent.pointerDown(canvas(), at("touch", 300, 400));
        fireEvent.pointerMove(canvas(), at("touch", 200, 300));
        expect(region.scrollLeft).toBe(0);
        fireEvent.pointerCancel(canvas(), at("touch", 200, 300));

        // On, the canvas holds every touch — and moves the view with the finger.
        await user.click(screen.getByRole("switch", { name: "Edit by touch" }));
        expect(canvas().getAttribute("class")).toContain("touch-none");
        for (const target of [canvas(), shape("z1")]) {
          region.scrollLeft = 0;
          pageScroller.scrollTop = 0;
          fireEvent.pointerDown(target, at("touch", 300, 400));
          // Inside the slop: still a tap in the making, nothing moves.
          fireEvent.pointerMove(target, at("touch", 296, 398));
          expect(region.scrollLeft).toBe(0);
          fireEvent.pointerMove(target, at("touch", 200, 300));
          expect(region.scrollLeft).toBe(100);
          expect(pageScroller.scrollTop).toBe(100);
          fireEvent.pointerMove(target, at("touch", 150, 340));
          expect(region.scrollLeft).toBe(150);
          expect(pageScroller.scrollTop).toBe(60);
          fireEvent.pointerUp(target, at("touch", 150, 340));
        }
        // A pan that started on a shape did not select it, move it, or resize it.
        expect(selected("Z1")).toBeNull();
        expect(shape("z1").getAttribute("x")).toBe("0");
        expect(screen.getByRole("button", { name: "Save draft" })).toHaveProperty("disabled", true);
      });

      it("drags the selected shape — and only that one — with Edit by touch on, leaving the rest of the canvas to pan", async () => {
        const user = userEvent.setup();
        planWithTwoShapes();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");
        await user.click(screen.getByRole("switch", { name: "Edit by touch" }));
        const region = screen.getByRole("group", { name: "Floor layout" });

        // A drag that starts on an unselected shape is not a drag of it.
        fireEvent.pointerDown(shape("z1"), at("touch", 100, 100));
        fireEvent.pointerMove(shape("z1"), at("touch", 400, 100));
        fireEvent.pointerUp(shape("z1"), at("touch", 400, 100));
        expect(shape("z1").getAttribute("x")).toBe("0");

        // Tapped, it is selected; now a drag that starts on it moves it, and the view stays put.
        tap(shape("z1"), 100, 100);
        region.scrollLeft = 0;
        fireEvent.pointerDown(shape("z1"), at("touch", 100, 100));
        fireEvent.pointerMove(shape("z1"), at("touch", 400, 150));
        fireEvent.pointerUp(shape("z1"), at("touch", 400, 150));
        expect(shape("z1").getAttribute("x")).toBe("300");
        expect(shape("z1").getAttribute("y")).toBe("50");
        expect(region.scrollLeft).toBe(0);
        // The draft has changed; nothing is saved until asked.
        expect(screen.getByRole("button", { name: "Save draft" })).toHaveProperty("disabled", false);
        expect(saveLayoutGeometry).not.toHaveBeenCalled();
      });

      it("resizes the selected shape by its handle, and a touch on another's handle only selects that shape", async () => {
        const user = userEvent.setup();
        planWithTwoShapes();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");
        await user.click(screen.getByRole("switch", { name: "Edit by touch" }));
        const handleOf = (id: string) => {
          const handle = shape(id).parentElement?.querySelector("rect.cursor-nwse-resize");
          if (!handle) throw new Error(`no handle for ${id}`);
          return handle;
        };

        tap(handleOf("z2"), 3900, 2750);
        expect(selected("Z2")).not.toBeNull();
        expect(shape("z2").getAttribute("width")).toBe("4000");

        tap(shape("z1"), 100, 100);
        fireEvent.pointerDown(handleOf("z1"), at("touch", 3900, 750));
        fireEvent.pointerMove(handleOf("z1"), at("touch", 5000, 1200));
        fireEvent.pointerUp(handleOf("z1"), at("touch", 5000, 1200));
        expect(shape("z1").getAttribute("width")).toBe("5000");
        expect(shape("z1").getAttribute("height")).toBe("1200");
      });

      it.each([false, true])("adds a walk node where a finger taps, never where it pans (Edit by touch %s)", async (switchedOn) => {
        const user = userEvent.setup();
        planWithTwoShapes();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");
        if (switchedOn) await user.click(screen.getByRole("switch", { name: "Edit by touch" }));
        await user.click(screen.getByRole("button", { name: "Add walk node" }));
        const before = nodeDots().length;

        // A pan: the browser ends it with a cancel; where the canvas pans by script it ends with a lift.
        fireEvent.pointerDown(canvas(), at("touch", 1000, 3500));
        fireEvent.pointerMove(canvas(), at("touch", 1000, 3200));
        if (switchedOn) fireEvent.pointerUp(canvas(), at("touch", 1000, 3200));
        else fireEvent.pointerCancel(canvas(), at("touch", 1000, 3200));
        expect(nodeDots()).toHaveLength(before);

        tap(canvas(), 1000, 3500);
        expect(nodeDots()).toHaveLength(before + 1);
      });

      it("connects two nodes by tapping each, and a pan that starts on a node connects nothing", async () => {
        const user = userEvent.setup();
        planWithTwoShapes();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");
        await user.click(screen.getByRole("button", { name: "Connect nodes" }));
        const [first, second] = nodeDots();
        if (!first || !second) throw new Error("expected the two walk nodes to be drawn");

        fireEvent.pointerDown(first, at("touch", 500, 1400));
        fireEvent.pointerCancel(first, at("touch", 500, 1400));
        expect(first.getAttribute("stroke")).not.toBe("var(--color-accent)");

        tap(first, 500, 1400);
        expect(first.getAttribute("stroke")).toBe("var(--color-accent)");

        fireEvent.pointerDown(second, at("touch", 3000, 1400));
        fireEvent.pointerCancel(second, at("touch", 3000, 1400));
        expect(screen.queryByText("New edge: J-1 → J-2")).toBeNull();

        tap(second, 3000, 1400);
        expect(await screen.findByText("New edge: J-1 → J-2")).toBeDefined();
      });

      it("leaves a mouse alone: it acts on press and drags whatever it presses, switch or no switch", async () => {
        const user = userEvent.setup();
        planWithTwoShapes();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");

        fireEvent.pointerDown(shape("z1"), at("mouse", 100, 100));
        expect(selected("Z1")).not.toBeNull();
        fireEvent.pointerMove(shape("z1"), at("mouse", 600, 100));
        fireEvent.pointerUp(shape("z1"), at("mouse", 600, 100));
        expect(shape("z1").getAttribute("x")).toBe("500");

        await user.click(screen.getByRole("button", { name: "Add walk node" }));
        const before = nodeDots().length;
        fireEvent.pointerDown(canvas(), at("mouse", 1000, 3500));
        expect(nodeDots()).toHaveLength(before + 1);
      });
    });

    describe("touch targets", () => {
      /** A phone-wide canvas holding two blocks 30 m apart: at Fit a millimetre is a hundredth of a pixel. */
      function wideFloor() {
        loadDraft(
          geometryOf({
            placements: [
              { location_id: "z1", x_mm: 0, y_mm: 0, width_mm: 4000, depth_mm: 3000 },
              { location_id: "z2", x_mm: 30000, y_mm: 0, width_mm: 4000, depth_mm: 3000 },
            ],
            walk_nodes: [{ code: "J-1", location_id: null, x_mm: 500, y_mm: 3600, z_mm: null }],
            walk_edges: [],
          }),
        );
      }

      function selectWithMouse(id: string) {
        fireEvent.pointerDown(shapeRect(id), { pointerType: "mouse", clientX: 100, clientY: 100 });
        fireEvent.pointerUp(shapeRect(id), { pointerType: "mouse", clientX: 100, clientY: 100 });
      }

      /** Screen pixels a length in millimetres comes to on the 360 x 360 canvas. */
      function pixels(mm: string | null): number {
        const [, , width, height] = (canvas().getAttribute("viewBox") ?? "").split(" ").map(Number) as [number, number, number, number];
        return Number(mm) * Math.min(360 / width, 360 / height);
      }

      const shapeRect = (id: string) => canvas().querySelector(`[data-placement-id="${id}"]`) as SVGRectElement;
      const handlesOf = (id: string) => Array.from(shapeRect(id).parentElement?.querySelectorAll("rect.cursor-nwse-resize") ?? []);

      beforeEach(() => {
        installCanvasPointerSupport();
        stubElementSize(360, 360);
        wideFloor();
      });
      afterEach(() => {
        vi.unstubAllGlobals();
        uninstallCanvasPointerSupport();
      });

      it("draws a resize handle and a walk node a few pixels across wherever the plan is scaled down", async () => {
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");

        // At 90 mm and 70 mm these were under a pixel on this canvas.
        const [handle] = handlesOf("z2");
        expect(pixels(handle?.getAttribute("width") ?? null)).toBeGreaterThanOrEqual(7.5);
        const [node] = Array.from(canvas().querySelectorAll("circle"));
        expect(pixels(node?.getAttribute("r") ?? null)).toBeGreaterThanOrEqual(4.8);
      });

      it("gives the selected shape's handle a touch target on a touch device, and no one else's", async () => {
        stubCoarsePointer();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");
        selectWithMouse("z1");

        const [pressArea, visible] = handlesOf("z1") as [Element, Element];
        expect(pressArea.getAttribute("fill")).toBe("none");
        expect(pixels(pressArea.getAttribute("width"))).toBeGreaterThanOrEqual(43.5);
        expect(pixels(visible.getAttribute("width"))).toBeGreaterThanOrEqual(13.5);
        // The unselected neighbour keeps its small handle alone: a target this size on every shape would cover them.
        expect(handlesOf("z2")).toHaveLength(1);
      });

      it("gives every walk node a touch target while connecting, on a touch device only", async () => {
        const user = userEvent.setup();
        stubCoarsePointer();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");
        await user.click(screen.getByRole("button", { name: "Connect nodes" }));

        const areas = Array.from(canvas().querySelectorAll("circle")).filter((c) => c.getAttribute("fill") === "none");
        expect(areas).toHaveLength(1);
        expect(pixels(areas[0]?.getAttribute("r") ?? null) * 2).toBeGreaterThanOrEqual(43.5);
      });

      it("adds no target beyond what is drawn when nothing points coarsely", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);
        await screen.findByText("Editing draft");
        const invisibleTargets = () =>
          Array.from(canvas().querySelectorAll("circle, rect")).filter((el) => el.getAttribute("fill") === "none");

        selectWithMouse("z1");
        expect(handlesOf("z1")).toHaveLength(1);
        expect(invisibleTargets()).toHaveLength(0);

        await user.click(screen.getByRole("button", { name: "Connect nodes" }));
        expect(invisibleTargets()).toHaveLength(0);
      });
    });

    describe("a location another floor already holds", () => {
      /** Floor 0 has published every location but Z2; this floor (1) has none placed. */
      const ON_FLOOR_ZERO = FLOOR_ZERO.filter((l) => l.id !== "z2");

      beforeEach(() => {
        listWarehouseFloors.mockResolvedValue([FLOOR, OTHER_FLOOR]);
        getPublishedLayout.mockResolvedValue(publishedLayoutOf(OTHER_FLOOR, ON_FLOOR_ZERO));
      });

      it("counts, offers and auto-arranges only the locations that are on no floor", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);

        expect(await screen.findByText("No locations on this floor yet")).toBeDefined();
        expect(screen.getByText(/6 more are already on another floor and left out/)).toBeDefined();
        expect(screen.queryByRole("button", { name: /Auto-arrange all/ })).toBeNull();
        await user.click(screen.getByRole("button", { name: "Auto-arrange 1 unplaced location" }));
        await user.click(screen.getByRole("button", { name: "Save draft" }));

        await waitFor(() => expect(saveLayoutGeometry).toHaveBeenCalledTimes(1));
        const [, payload] = saveLayoutGeometry.mock.calls[0] as [string, LayoutGeometryIn];
        // Z2 only: publishing this would otherwise have moved the other six onto floor 1.
        expect(payload.placements.map((p) => p.location_id)).toEqual(["z2"]);
        // Only the other floors' published layouts are read, never this floor's own.
        expect(getPublishedLayout).toHaveBeenCalledWith("floor-0");
        expect(getPublishedLayout).not.toHaveBeenCalledWith("floor-1");
      });

      it("lists them apart, under their floor, and asks before placing one", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);

        const list = await screen.findByRole("region", { name: "Locations on other floors" });
        expect(screen.getByRole("button", { name: "Unplaced locations 1" })).toBeDefined();
        expect(within(list).getAllByRole("button", { name: "Place here too" })).toHaveLength(6);
        expect(within(list).getAllByText("Floor 0 — Mezzanine")).toHaveLength(6);
        // The plain Place is only for what is on no floor.
        expect(screen.getAllByRole("button", { name: "Place" })).toHaveLength(1);

        await user.click(within(list).getAllByRole("button", { name: "Place here too" })[0] as HTMLElement);
        expect(await screen.findByText("Place Z1 on this floor too?")).toBeDefined();
        expect(screen.getByText(/Z1 is already on Floor 0 — Mezzanine's published layout/)).toBeDefined();
        await user.click(screen.getByRole("button", { name: "Cancel" }));
        expect(screen.queryByText("Place Z1 on this floor too?")).toBeNull();
        expect(screen.getByText("No locations on this floor yet")).toBeDefined();

        await user.click(within(list).getAllByRole("button", { name: "Place here too" })[0] as HTMLElement);
        const dialog = await screen.findByRole("dialog");
        await user.click(within(dialog).getByRole("button", { name: "Place here too" }));
        expect(await screen.findByText("Placed Z1")).toBeDefined();
        expect(Array.from(canvas().querySelectorAll("text")).map((t) => t.textContent)).toEqual(["Z1"]);
      });

      it("has nothing to auto-arrange when every location is on another floor, and says so", async () => {
        getPublishedLayout.mockResolvedValue(publishedLayoutOf(OTHER_FLOOR, FLOOR_ZERO));
        renderPage([WRITE_PERM]);

        expect(await screen.findByText(/Every location in this warehouse is already on another floor/)).toBeDefined();
        expect(screen.queryByRole("button", { name: /Auto-arrange (all|\d)/ })).toBeNull();
        expect(screen.getByRole("button", { name: "Show locations on other floors" })).toBeDefined();
        expect(screen.getByRole("button", { name: "Auto-arrange unplaced" })).toHaveProperty("disabled", true);
        expect(screen.getByText(/every other location in this warehouse is on another floor/)).toBeDefined();
      });

      it("offers and arranges nothing until it knows which floors hold what", async () => {
        getPublishedLayout.mockReturnValue(new Promise(() => undefined));
        renderPage([WRITE_PERM]);

        await screen.findByText("Editing draft");
        await waitFor(() => expect(getPublishedLayout).toHaveBeenCalled());
        expect(screen.queryByText("No locations on this floor yet")).toBeNull();
        expect(screen.queryByRole("button", { name: /Auto-arrange all/ })).toBeNull();
        expect(screen.queryByRole("button", { name: "Place" })).toBeNull();
        expect(screen.getByRole("button", { name: "Auto-arrange unplaced" })).toHaveProperty("disabled", true);
      });

      it("will not guess when the other floors cannot be read, and recovers on Try again", async () => {
        const user = userEvent.setup();
        getPublishedLayout.mockRejectedValueOnce(new Error("offline"));
        renderPage([WRITE_PERM]);

        expect(await screen.findByText("Couldn't check the warehouse's other floors")).toBeDefined();
        expect(screen.queryByRole("button", { name: /Auto-arrange all/ })).toBeNull();
        expect(screen.queryByRole("button", { name: "Place" })).toBeNull();
        expect(screen.getByRole("button", { name: "Auto-arrange unplaced" })).toHaveProperty("disabled", true);

        const retries = screen.getAllByRole("button", { name: "Try again" });
        await user.click(retries[0] as HTMLElement);
        expect(await screen.findByRole("button", { name: "Auto-arrange 1 unplaced location" })).toBeDefined();
      });

      it("reads no other floor's layout in a warehouse of one floor", async () => {
        listWarehouseFloors.mockResolvedValue([FLOOR]);
        renderPage([WRITE_PERM]);

        expect(await screen.findByRole("button", { name: "Auto-arrange all 7 unplaced locations" })).toBeDefined();
        expect(getPublishedLayout).not.toHaveBeenCalled();
        expect(screen.queryByRole("region", { name: "Locations on other floors" })).toBeNull();
      });

      it("is not told a location is elsewhere because it is on this floor's own published layout", async () => {
        getPublishedLayout.mockImplementation((id: string) =>
          Promise.resolve(publishedLayoutOf(id === "floor-0" ? OTHER_FLOOR : FLOOR, id === "floor-0" ? [] : FLOOR_ZERO)),
        );
        renderPage([WRITE_PERM]);

        expect(await screen.findByRole("button", { name: "Auto-arrange all 7 unplaced locations" })).toBeDefined();
      });
    });

    describe("walk nodes and the empty state", () => {
      beforeEach(() => installCanvasPointerSupport());
      afterEach(() => {
        vi.unstubAllGlobals();
        uninstallCanvasPointerSupport();
      });

      it("says how many locations have no walk node, and does not offer them in Try a route", async () => {
        const user = userEvent.setup();
        const published: LayoutVersionOut = { ...draft, id: "v-pub", status: "published", version_number: 1, published_at: "2026-09-01T00:00:00Z" };
        listLayoutVersions.mockResolvedValue([draft, published]);
        renderPage([WRITE_PERM]);

        // One by Place, which draws its node; the other six by Auto-arrange, which does not.
        await user.click((await screen.findAllByRole("button", { name: "Place" }))[0] as HTMLElement);
        expect(screen.queryByText(/have no walk node/)).toBeNull();
        await user.click(screen.getByRole("button", { name: "Auto-arrange unplaced" }));

        expect(screen.getByText(/6 locations have no walk node, so a picker can't be routed to them/)).toBeDefined();
        expect(screen.getByText("6 locations are not listed: they have no walk node.")).toBeDefined();
        const options = within(screen.getByLabelText("From")).getAllByRole("option").map((o) => o.textContent);
        expect(options).toEqual(["Choose a location", "Z1 — Zone"]);
      });

      it("keeps saying nothing is placed when only a walk node is on the canvas, and offers no zoom", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);
        await screen.findByText("No locations on this floor yet");
        await user.click(screen.getByRole("button", { name: "Add walk node" }));
        fireEvent.pointerDown(canvas(), { clientX: 100, clientY: 100 });

        expect(canvas().querySelectorAll("circle").length).toBeGreaterThan(0);
        expect(screen.getByText("No locations on this floor yet")).toBeDefined();
        expect(screen.getByRole("button", { name: "Auto-arrange all 7 unplaced locations" })).toBeDefined();
        expect(screen.queryByRole("button", { name: "Zoom in" })).toBeNull();
      });

      it("keeps the two ways forward stacked, whatever the width, and points to the list by name", async () => {
        renderPage([WRITE_PERM]);
        const arrange = await screen.findByRole("button", { name: "Auto-arrange all 7 unplaced locations" });

        const row = arrange.parentElement as HTMLElement;
        expect(row.className).toContain("flex-col");
        expect(row.className).not.toContain("flex-row");
        const sentence = screen.getByText(/unplaced locations in this warehouse/).textContent ?? "";
        expect(sentence).toContain("Unplaced locations list");
        expect(sentence).not.toContain("below");
      });
    });
  });

  describe("version history", () => {
    it("lists versions and rolls back an archived one", async () => {
      const user = userEvent.setup();
      const published: LayoutVersionOut = {
        id: "v2",
        warehouse_floor_id: "floor-1",
        version_number: 2,
        status: "published",
        label: "",
        published_at: "2026-09-02T00:00:00Z",
        published_by: "user-1",
        created_at: "2026-09-02T00:00:00Z",
        updated_at: "2026-09-02T00:00:00Z",
      };
      const archived: LayoutVersionOut = {
        id: "v1",
        warehouse_floor_id: "floor-1",
        version_number: 1,
        status: "archived",
        label: "",
        published_at: "2026-09-01T00:00:00Z",
        published_by: "user-1",
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      };
      listLayoutVersions.mockResolvedValue([published, archived]);
      getLayoutGeometry.mockResolvedValue(EMPTY_GEOMETRY(published));
      publishLayoutVersion.mockResolvedValue({ ...archived, status: "published" });

      renderPage([WRITE_PERM, PUBLISH_PERM]);
      await screen.findByText("Viewing the published layout — read-only");

      await user.click(screen.getByRole("button", { name: "Version history" }));
      expect(await screen.findByText("Version 2")).toBeDefined();
      expect(screen.getByText("Version 1")).toBeDefined();

      const rollbackButton = screen.getByRole("button", { name: "Roll back" });
      await user.click(rollbackButton);
      const confirmButtons = await screen.findAllByRole("button", { name: "Roll back" });
      const confirmButton = confirmButtons.find((b) => b !== rollbackButton);
      if (!confirmButton) throw new Error("expected a second Roll back button, in the confirm dialog");
      await user.click(confirmButton);

      await waitFor(() => expect(publishLayoutVersion).toHaveBeenCalledWith("v1"));
    });
  });
});
