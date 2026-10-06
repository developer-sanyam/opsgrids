/**
 * The 3-D panel's three ways of not showing a scene, each with its own accurate words: a browser
 * with no WebGL (nothing to retry), a scene that throws while drawing, and a scene whose code never
 * downloaded. The last two offer "Try again", and the retry has to be able to recover, including
 * from a chunk that failed to download.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { PublishedLayoutOut } from "@/api/types";
import { resetReports } from "@/lib/clientReport";
import { Floor3DPanel } from "./Floor3DPanel";

const hasWebGLSupport = vi.fn();
let sceneThrows = false;
let chunkFailures = 0;

vi.mock("@/lib/webgl", () => ({ hasWebGLSupport: () => hasWebGLSupport() }));
// A getter, not a plain export: reading it is what a dynamic import's `.then` does, and a getter
// that throws is a chunk that failed to download.
vi.mock("@/routes/Warehouse3DScene", () => ({
  get Warehouse3DScene() {
    if (chunkFailures > 0) {
      chunkFailures -= 1;
      throw new Error("Failed to fetch dynamically imported module");
    }
    return function SceneStub() {
      if (sceneThrows) throw new Error("three.js blew up");
      return <div data-testid="scene-stub" />;
    };
  },
}));

const PUBLISHED: PublishedLayoutOut = {
  floor_id: "floor-1",
  warehouse_id: "wh-1",
  floor_name: "Ground",
  published: true,
  layout_version_id: "lv-1",
  placements: [
    { location_id: "bin-a", code: "A-01", name: "Bin A-01", kind: "bin", parent_id: null, x_mm: 0, y_mm: 0, z_mm: null, width_mm: 1000, depth_mm: 800, height_mm: null },
  ],
};

function renderPanel() {
  return render(
    <Floor3DPanel
      warehouseId="wh-1"
      floorId="floor-1"
      published={PUBLISHED}
      highlightIds={new Set()}
      selectedId={null}
      onSelect={vi.fn()}
      unavailableAction={<button type="button">Show the 2-D plan</button>}
    />,
  );
}

describe("Floor3DPanel failures", () => {
  beforeEach(() => {
    resetReports();
    sceneThrows = false;
    chunkFailures = 0;
    hasWebGLSupport.mockReset().mockReturnValue(true);
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  // First on purpose: the scene's lazy component is module state, as it is in the app, and once any
  // test has loaded it a later one cannot make it fail. This one fails twice to show a retry can
  // itself fail and be retried, and it checks the failures were used up so it cannot pass vacuously.
  it("recovers from a scene chunk that failed to download, by downloading it again, however often it fails first", async () => {
    chunkFailures = 2;
    const user = userEvent.setup();
    renderPanel();

    expect(await screen.findByText("The 3D view couldn't be drawn")).toBeDefined();
    expect(screen.getByText(/dropped connection/)).toBeDefined();
    expect(chunkFailures).toBe(1);

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("The 3D view couldn't be drawn")).toBeDefined();
    expect(chunkFailures).toBe(0);

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("scene-stub")).toBeDefined();
  });

  it("says the browser has no WebGL, with no retry, since a retry cannot add it", async () => {
    hasWebGLSupport.mockReturnValue(false);
    renderPanel();
    expect(await screen.findByText("This browser can't show the 3D view")).toBeDefined();
    expect(screen.getByText(/WebGL isn't available here/)).toBeDefined();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    expect(screen.getByRole("button", { name: "Show the 2-D plan" })).toBeDefined();
  });

  it("does not blame the browser when the scene throws: it says the view could not be drawn, and offers a retry that works", async () => {
    sceneThrows = true;
    const user = userEvent.setup();
    renderPanel();

    expect(await screen.findByText("The 3D view couldn't be drawn")).toBeDefined();
    expect(screen.queryByText("This browser can't show the 3D view")).toBeNull();
    expect(screen.queryByText(/WebGL isn't available/)).toBeNull();
    expect(screen.getByRole("button", { name: "Show the 2-D plan" })).toBeDefined();

    sceneThrows = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("scene-stub")).toBeDefined();
    expect(screen.queryByText("The 3D view couldn't be drawn")).toBeNull();
  });

});
