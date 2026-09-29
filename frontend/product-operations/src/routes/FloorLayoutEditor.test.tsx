/**
 * The floor layout editor (Fulfilment plan §2/F7). Real pointer-drag canvas interaction is not
 * exercised here — jsdom has no `getScreenCTM`, so the coordinate math a real browser drag needs
 * cannot run in this environment — but every button-driven part of the flow the acceptance
 * scenario cares about is: opening a draft, placing a location (auto-creating its walk node),
 * saving, validating, publishing, and rolling back from the version history.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";

import type {
  LayoutGeometryOut,
  LayoutValidationOut,
  LayoutVersionOut,
  LocationOut,
  WarehouseFloorOut,
} from "@/api/types";
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
