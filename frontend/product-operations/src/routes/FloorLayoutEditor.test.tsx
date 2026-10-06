/**
 * The floor layout editor (Fulfilment plan §2/F7). Real pointer-drag canvas interaction is not
 * exercised here — jsdom has no `getScreenCTM`, so the coordinate math a real browser drag needs
 * cannot run in this environment — but every button-driven part of the flow the acceptance
 * scenario cares about is: opening a draft, placing a location (auto-creating its walk node),
 * saving, validating, publishing, and rolling back from the version history.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
  WarehouseFloorOut,
} from "@/api/types";
import { overlappingPlacements, validateOverlaps } from "@/test-utils/floorOverlap";
import { FloorLayoutEditorPage } from "./FloorLayoutEditor";

const getWarehouseFloor = vi.fn();
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
      it("draws the new rectangle on the canvas, selects it, announces it and brings the canvas into view", async () => {
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
        expect(scrollIntoView).toHaveBeenCalledWith(expect.objectContaining({ block: "nearest" }));
        expect(screen.getByRole("group", { name: "Floor layout" })).toBe(document.activeElement);
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

    describe("on a touch screen", () => {
      it("lets the page scroll over the canvas until Edit by touch is switched on", async () => {
        const user = userEvent.setup();
        renderPage([WRITE_PERM]);
        await user.click(await screen.findByRole("button", { name: "Auto-arrange all 7 unplaced locations" }));

        expect(canvas().getAttribute("class")).toContain("touch-auto");
        expect(canvas().getAttribute("class")).not.toContain("touch-none");

        await user.click(screen.getByRole("switch", { name: "Edit by touch" }));
        expect(canvas().getAttribute("class")).toContain("touch-none");

        await user.click(screen.getByRole("switch", { name: "Edit by touch" }));
        expect(canvas().getAttribute("class")).toContain("touch-auto");
      });

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
