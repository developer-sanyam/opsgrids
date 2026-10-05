/**
 * The 2-D floor plan, drawn from real published placements: one button per location, highlight that
 * is outline + pin + label + words (never colour alone), selection by pointer and keyboard, and the
 * pan and zoom controls a keyboard can reach.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { PublishedPlacementOut } from "@/api/types";
import { normalizePlacements } from "@/lib/floorGeometry";
import { FloorPlan2D } from "./FloorPlan2D";

function placement(overrides: Partial<PublishedPlacementOut>): PublishedPlacementOut {
  return {
    location_id: "loc",
    code: "X",
    name: "X",
    kind: "bin",
    parent_id: null,
    x_mm: 0,
    y_mm: 0,
    z_mm: null,
    width_mm: 1000,
    depth_mm: 800,
    height_mm: null,
    ...overrides,
  };
}

const RACK = placement({ location_id: "rack", code: "RACK-1", name: "Rack 1", kind: "rack", x_mm: 0, y_mm: 0, width_mm: 6000, depth_mm: 1200 });
const BIN_A = placement({ location_id: "bin-a", code: "A-01", name: "Bin A-01", x_mm: 200, y_mm: 200, width_mm: 1000, depth_mm: 800, parent_id: "rack" });
const BIN_B = placement({ location_id: "bin-b", code: "A-02", name: "Bin A-02", x_mm: 1500, y_mm: 200, width_mm: 1000, depth_mm: 800, parent_id: "rack" });

const { drawable } = normalizePlacements([BIN_A, RACK, BIN_B]);

function renderPlan(props: Partial<Parameters<typeof FloorPlan2D>[0]> = {}) {
  const onSelect = vi.fn();
  render(
    <FloorPlan2D
      rects={drawable}
      highlightIds={new Set()}
      selectedId={null}
      onSelect={onSelect}
      label="Floor plan of Ground"
      {...props}
    />,
  );
  return { onSelect };
}

function viewBox(): number[] {
  return (screen.getByRole("group", { name: "Floor plan of Ground" }).getAttribute("viewBox") ?? "").split(" ").map(Number);
}

describe("FloorPlan2D", () => {
  it("draws one button per placed location, each named by code and kind", () => {
    renderPlan();
    expect(screen.getByRole("button", { name: "RACK-1, Rack" })).toBeDefined();
    expect(screen.getByRole("button", { name: "A-01, Bin" })).toBeDefined();
    expect(screen.getByRole("button", { name: "A-02, Bin" })).toBeDefined();
    expect(screen.getAllByRole("button", { name: /, (Rack|Bin)$/ })).toHaveLength(3);
  });

  it("draws the rack before the bins on it, so the bins are not hidden underneath", () => {
    renderPlan();
    const order = screen
      .getAllByRole("button", { name: /, (Rack|Bin)$/ })
      .map((node) => node.getAttribute("aria-label")?.split(",")[0]);
    expect(order).toEqual(["RACK-1", "A-01", "A-02"]);
  });

  it("reports a click and an Enter or Space press on a location", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderPlan();
    await user.click(screen.getByRole("button", { name: "A-01, Bin" }));
    expect(onSelect).toHaveBeenLastCalledWith("bin-a");

    screen.getByRole("button", { name: "A-02, Bin" }).focus();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenLastCalledWith("bin-b");
    await user.keyboard(" ");
    expect(onSelect).toHaveBeenCalledTimes(3);
  });

  it("marks the selected location with aria-pressed", () => {
    renderPlan({ selectedId: "bin-b" });
    expect(screen.getByRole("button", { name: "A-02, Bin" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "A-01, Bin" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("highlights what a search found with a heavy dashed outline, a pin and a label — and says so in its name", () => {
    renderPlan({ highlightIds: new Set(["bin-a", "bin-b"]) });
    const found = screen.getByRole("button", { name: "A-01, Bin, found by your search" });
    const rect = found.querySelector("rect");
    expect(rect?.getAttribute("data-highlighted")).toBe("true");
    expect(rect?.getAttribute("stroke-dasharray")).toBeTruthy();
    expect(found.querySelector("path[data-pin='true']")).not.toBeNull();
    expect(found.textContent).toContain("A-01");

    expect(screen.getByRole("button", { name: "A-02, Bin, found by your search" })).toBeDefined();
    const plain = screen.getByRole("button", { name: "RACK-1, Rack" });
    expect(plain.querySelector("rect")?.getAttribute("data-highlighted")).toBeNull();
    expect(plain.querySelector("path[data-pin='true']")).toBeNull();
  });

  it("highlights several locations at once", () => {
    renderPlan({ highlightIds: new Set(["bin-a", "bin-b", "rack"]) });
    expect(document.querySelectorAll("path[data-pin='true']")).toHaveLength(3);
  });

  it("zooms with the buttons and resets, and disables a button at its limit", async () => {
    const user = userEvent.setup();
    renderPlan();
    const [, , w0, h0] = viewBox();
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
    const [, , w1, h1] = viewBox();
    expect(w1).toBeLessThan(w0 ?? 0);
    expect(h1).toBeLessThan(h0 ?? 0);

    await user.click(screen.getByRole("button", { name: "Zoom out" }));
    await user.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(viewBox()[2]).toBeGreaterThan(w0 ?? 0);

    await user.click(screen.getByRole("button", { name: "Reset view" }));
    expect(viewBox()).toEqual([...viewBox().slice(0, 2), w0, h0]);

    for (let i = 0; i < 12; i += 1) {
      const zoomIn = screen.getByRole("button", { name: "Zoom in" }) as HTMLButtonElement;
      if (zoomIn.disabled) break;
      await user.click(zoomIn);
    }
    expect((screen.getByRole("button", { name: "Zoom in" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("pans and zooms from the keyboard when the plan itself has focus", async () => {
    const user = userEvent.setup();
    renderPlan();
    const plan = screen.getByRole("group", { name: "Floor plan of Ground" });
    plan.focus();
    const [x0, y0, w0] = viewBox();
    await user.keyboard("{ArrowRight}");
    expect(viewBox()[0]).toBeGreaterThan(x0 ?? 0);
    await user.keyboard("{ArrowDown}");
    expect(viewBox()[1]).toBeGreaterThan(y0 ?? 0);
    await user.keyboard("+");
    expect(viewBox()[2]).toBeLessThan(w0 ?? 0);
    await user.keyboard("0");
    expect(viewBox()[2]).toBe(w0);
  });

  it("leaves the arrow keys to a focused location rather than panning the plan", async () => {
    const user = userEvent.setup();
    renderPlan();
    const before = viewBox();
    screen.getByRole("button", { name: "A-01, Bin" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(viewBox()).toEqual(before);
  });

  it("zooms with Ctrl and the wheel but leaves a plain wheel to the page", () => {
    renderPlan();
    const plan = screen.getByRole("group", { name: "Floor plan of Ground" });
    const w0 = viewBox()[2];
    fireEvent.wheel(plan, { deltaY: -100 });
    expect(viewBox()[2]).toBe(w0);
    fireEvent.wheel(plan, { deltaY: -100, ctrlKey: true });
    expect(viewBox()[2]).toBeLessThan(w0 ?? 0);
  });

  it("takes over a Ctrl/⌘ wheel from the browser, whose own page zoom would otherwise scale the whole app", () => {
    renderPlan();
    const plan = screen.getByRole("group", { name: "Floor plan of Ground" });
    // `fireEvent` returns false when a listener cancelled the event. A listener the browser was told
    // is passive cannot cancel it, which is what a React `onWheel` is: React registers wheel
    // listeners passive, so `preventDefault` there is ignored and the page zooms with the plan.
    expect(fireEvent.wheel(plan, { deltaY: -100, ctrlKey: true, cancelable: true })).toBe(false);
    expect(fireEvent.wheel(plan, { deltaY: -100, metaKey: true, cancelable: true })).toBe(false);
    expect(fireEvent.wheel(plan, { deltaY: -100, cancelable: true })).toBe(true);
  });

  it("registers its wheel listener as non-passive, the only kind that may cancel the browser's zoom", () => {
    const registered: Array<boolean | AddEventListenerOptions | undefined> = [];
    const original = SVGSVGElement.prototype.addEventListener;
    vi.spyOn(SVGSVGElement.prototype, "addEventListener").mockImplementation(function (
      this: SVGSVGElement,
      type: string,
      listener: EventListenerOrEventListenerObject,
      options?: boolean | AddEventListenerOptions,
    ) {
      if (type === "wheel") registered.push(options);
      original.call(this, type, listener, options);
    });
    renderPlan();
    expect(registered).toHaveLength(1);
    expect(registered[0]).toMatchObject({ passive: false });
  });

  it("stops listening for the wheel when the plan goes away", () => {
    const removed: string[] = [];
    const original = SVGSVGElement.prototype.removeEventListener;
    vi.spyOn(SVGSVGElement.prototype, "removeEventListener").mockImplementation(function (
      this: SVGSVGElement,
      type: string,
      listener: EventListenerOrEventListenerObject,
      options?: boolean | EventListenerOptions,
    ) {
      removed.push(type);
      original.call(this, type, listener, options);
    });
    const { unmount } = render(
      <FloorPlan2D rects={drawable} highlightIds={new Set()} selectedId={null} onSelect={vi.fn()} label="Floor plan of Ground" />,
    );
    unmount();
    expect(removed).toContain("wheel");
  });

  it("explains how to pan and zoom in words", () => {
    renderPlan();
    expect(screen.getByText(/Drag to pan/)).toBeDefined();
  });
});
