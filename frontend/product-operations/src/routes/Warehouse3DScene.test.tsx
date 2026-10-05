/**
 * The 3-D scene itself, built by the real `@react-three/fiber` reconciler into a real three.js scene
 * graph. Only what jsdom cannot do is stood in for: the WebGL renderer (there is none) and troika's
 * text (it wants a worker and a font), which becomes a plain mesh that keeps the props the scene
 * gives it. What is pinned is that graph, because that is where a highlight either can be seen or
 * cannot: the pixels are the GPU's, but whether a material tests depth, and in what order things
 * are drawn, is decided here.
 */
import { useLayoutEffect, useRef } from "react";
import type { ReactNode } from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as THREE from "three";

import type { WarehouseLayoutNode } from "@/lib/warehouseLayout";
import { Warehouse3DScene } from "./Warehouse3DScene";

const built: { scene: THREE.Scene | null } = { scene: null };

vi.mock("@react-three/fiber", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@react-three/fiber")>();
  /** The real `Canvas` wants a WebGL context; this one mounts the same children through the same
   *  reconciler, onto a renderer that draws nothing, and keeps the scene it builds. */
  function Canvas({ children }: { children?: ReactNode }) {
    const ref = useRef<HTMLCanvasElement>(null);
    useLayoutEffect(() => {
      const canvas = ref.current;
      if (!canvas) return;
      const nothing = () => undefined;
      const renderer = {
        domElement: canvas,
        render: nothing,
        setSize: nothing,
        setPixelRatio: nothing,
        setClearColor: nothing,
        dispose: nothing,
        forceContextLoss: nothing,
        getContext: () => null,
        shadowMap: {},
        xr: { enabled: false, addEventListener: nothing, removeEventListener: nothing, setAnimationLoop: nothing, connect: nothing, disconnect: nothing },
      };
      // The real `Canvas` makes the whole THREE namespace available as JSX (`<mesh>`, `<boxGeometry>`).
      actual.extend(THREE as unknown as Parameters<typeof actual.extend>[0]);
      const root = actual.createRoot(canvas);
      void root.configure({
        gl: renderer as unknown as THREE.WebGLRenderer,
        frameloop: "never",
        size: { width: 800, height: 600, top: 0, left: 0 },
      });
      built.scene = root.render(children).getState().scene;
      return () => root.unmount();
    });
    return <canvas ref={ref} />;
  }
  return { ...actual, Canvas };
});

vi.mock("@react-three/drei", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@react-three/drei")>();
  /** A mesh named for its text, taking the same props `Text` does (`renderOrder`, `material-depthTest`). */
  function Text({ children, ...props }: { children?: ReactNode } & Record<string, unknown>) {
    return <mesh name={`text:${String(children)}`} {...props} />;
  }
  return { ...actual, Text };
});

type Kind = WarehouseLayoutNode["location"]["kind"];

function node(id: string, code: string, kind: Kind, position: [number, number, number], size: [number, number, number]): WarehouseLayoutNode {
  return { location: { id, parent_id: null, kind, code, name: code }, position, size };
}

/** A 2.4 m x 2 m x 1 m rack, 2 m tall, with a 0.8 m bin inside it: the bin's top (1 m) and even its
 *  outline (1.125 m) are lower than the rack's top, so a depth-tested outline is inside the rack. */
const RACK = node("rack", "RACK-1", "rack", [1, 1, 1], [2.4, 2, 1]);
const BIN = node("bin-3", "B-03", "bin", [1, 0.5, 1], [0.8, 1, 0.8]);
const BIN_4 = node("bin-4", "B-04", "bin", [2, 0.5, 1], [0.8, 1, 0.8]);

function renderScene(layout: WarehouseLayoutNode[], highlightIds: string[]): THREE.Scene {
  render(<Warehouse3DScene layout={layout} selectedId={null} highlightIds={new Set(highlightIds)} onSelect={() => undefined} />);
  if (!built.scene) throw new Error("the scene was not built");
  return built.scene;
}

function meshes(scene: THREE.Scene): THREE.Mesh[] {
  const found: THREE.Mesh[] = [];
  scene.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) found.push(o as THREE.Mesh);
  });
  return found;
}

function materialOf(mesh: THREE.Mesh): THREE.Material {
  return Array.isArray(mesh.material) ? (mesh.material[0] as THREE.Material) : mesh.material;
}

/** The wireframe box drawn around a highlighted location: 1.25 x its size. */
function outlines(scene: THREE.Scene): THREE.Mesh[] {
  return meshes(scene).filter((m) => materialOf(m).type === "MeshBasicMaterial" && (materialOf(m) as THREE.MeshBasicMaterial).wireframe);
}

function labelNamed(scene: THREE.Scene, text: string): THREE.Mesh | undefined {
  return meshes(scene).find((m) => m.name === `text:${text}`);
}

/** The instanced boxes: one per kind present. */
function boxes(scene: THREE.Scene): THREE.InstancedMesh[] {
  return meshes(scene).filter((m): m is THREE.InstancedMesh => (m as THREE.InstancedMesh).isInstancedMesh === true);
}

beforeEach(() => {
  // three.js warns, once per root, that the `Clock` the reconciler creates is deprecated. That is the
  // library's own notice, not something this app can act on; any other warning still comes through.
  const warn = console.warn.bind(console);
  vi.spyOn(console, "warn").mockImplementation((...args: unknown[]) => {
    if (typeof args[0] === "string" && args[0].startsWith("THREE.Clock")) return;
    warn(...args);
  });
});

afterEach(() => {
  cleanup();
  built.scene = null;
});

describe("Warehouse3DScene highlight", () => {
  it("draws a match's outline and name without depth testing, after every box, so a container around it cannot hide them", () => {
    const scene = renderScene([RACK, BIN], ["bin-3"]);

    // The case this exists for: the outline is wholly inside the rack, so a depth-tested one is hidden.
    const [outline] = outlines(scene);
    if (!outline) throw new Error("no outline was drawn for the highlighted bin");
    const outlineTop = outline.position.y + (outline.geometry as THREE.BoxGeometry).parameters.height / 2;
    expect(outlineTop).toBeLessThan(RACK.position[1] + RACK.size[1] / 2);

    expect(materialOf(outline).depthTest).toBe(false);
    const label = labelNamed(scene, "Found: B-03");
    if (!label) throw new Error("no 'Found' label was drawn for the highlighted bin");
    expect(materialOf(label).depthTest).toBe(false);

    const drawnFirst = Math.max(...boxes(scene).map((b) => b.renderOrder));
    expect(outline.renderOrder).toBeGreaterThan(drawnFirst);
    expect(label.renderOrder).toBeGreaterThan(drawnFirst);
  });

  it("keeps the boxes themselves depth-tested, so only the highlight shows through, never the whole scene", () => {
    const scene = renderScene([RACK, BIN], ["bin-3"]);
    expect(boxes(scene).length).toBeGreaterThan(0);
    for (const box of boxes(scene)) expect(materialOf(box).depthTest).toBe(true);
    // The ordinary root labels are depth-tested too: only a match is called out through geometry.
    const rackLabel = labelNamed(scene, "RACK-1");
    expect(rackLabel).toBeDefined();
    if (rackLabel) expect(materialOf(rackLabel).depthTest).toBe(true);
  });

  it("gives every match its own outline and name", () => {
    const scene = renderScene([RACK, BIN, BIN_4], ["bin-3", "bin-4"]);
    expect(outlines(scene)).toHaveLength(2);
    for (const outline of outlines(scene)) expect(materialOf(outline).depthTest).toBe(false);
    expect(labelNamed(scene, "Found: B-03")).toBeDefined();
    expect(labelNamed(scene, "Found: B-04")).toBeDefined();
  });

  it("draws no outline and no 'Found' label when nothing is highlighted", () => {
    const scene = renderScene([RACK, BIN], []);
    expect(outlines(scene)).toHaveLength(0);
    expect(meshes(scene).some((m) => m.name.startsWith("text:Found"))).toBe(false);
  });
});
