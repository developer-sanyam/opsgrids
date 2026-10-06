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

  describe("dragging the plan", () => {
    const MOUSE = { pointerId: 1, pointerType: "mouse", button: 0 };

    function stubPlanSize() {
      vi.spyOn(SVGSVGElement.prototype, "getBoundingClientRect").mockReturnValue({
        x: 0,
        y: 0,
        left: 0,
        top: 0,
        right: 800,
        bottom: 600,
        width: 800,
        height: 600,
        toJSON: () => ({}),
      });
    }

    /**
     * jsdom implements no `PointerEvent`, and Testing Library's fallback drops `clientX` and
     * `button`. A `MouseEvent` carries both, and React maps it to `onPointerDown` and the rest by
     * type, as it does a browser's own.
     */
    function pointer(target: Element, type: "pointerdown" | "pointermove" | "pointerup", x: number, y: number, init: typeof MOUSE) {
      const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: init.button });
      Object.defineProperty(event, "pointerId", { value: init.pointerId });
      Object.defineProperty(event, "pointerType", { value: init.pointerType });
      fireEvent(target, event);
    }

    /** A press on `target`, a move of (dx, dy) screen pixels, and the release. */
    function press(target: Element, dx: number, dy: number, init = MOUSE) {
      pointer(target, "pointerdown", 100, 100, init);
      pointer(target, "pointermove", 100 + dx, 100 + dy, init);
      pointer(target, "pointerup", 100 + dx, 100 + dy, init);
    }

    it("pans when the drag starts on the background", () => {
      stubPlanSize();
      renderPlan();
      const before = viewBox();
      press(screen.getByRole("group", { name: "Floor plan of Ground" }), -80, 0);
      expect(viewBox()[0]).toBeGreaterThan(before[0] ?? 0);
      expect(viewBox()[2]).toBe(before[2]);
    });

    it("pans when the drag starts on a location, as it must once the plan is zoomed in and locations fill it", () => {
      stubPlanSize();
      const { onSelect } = renderPlan();
      const before = viewBox();
      press(screen.getByRole("button", { name: "A-01, Bin" }), -80, 40);
      expect(viewBox()[0]).toBeGreaterThan(before[0] ?? 0);
      expect(viewBox()[1]).toBeLessThan(before[1] ?? 0);
      expect(onSelect).not.toHaveBeenCalled();
    });

    it("does not open the location a drag began on when the browser still sends the click, and the next click works", () => {
      stubPlanSize();
      const { onSelect } = renderPlan();
      const bin = screen.getByRole("button", { name: "A-01, Bin" });
      press(bin, -80, 0);
      fireEvent.click(bin);
      expect(onSelect).not.toHaveBeenCalled();

      press(bin, 0, 0);
      fireEvent.click(bin);
      expect(onSelect).toHaveBeenCalledTimes(1);
      expect(onSelect).toHaveBeenCalledWith("bin-a");
    });

    it("does not let a drag that ended elsewhere swallow the next plain click on a location", () => {
      stubPlanSize();
      const { onSelect } = renderPlan();
      const bin = screen.getByRole("button", { name: "A-02, Bin" });
      press(bin, -80, 0);
      // No click arrived for it (the browser sent it to the captured plan). A fresh press resets.
      press(bin, 0, 0);
      fireEvent.click(bin);
      expect(onSelect).toHaveBeenCalledWith("bin-b");
    });

    it("treats a press that moves only a few pixels as a click, and does not pan", () => {
      stubPlanSize();
      const { onSelect } = renderPlan();
      const before = viewBox();
      const bin = screen.getByRole("button", { name: "A-01, Bin" });
      press(bin, 3, 2);
      fireEvent.click(bin);
      expect(onSelect).toHaveBeenCalledWith("bin-a");
      expect(viewBox()).toEqual(before);
    });

    it("ignores a drag with the right mouse button", () => {
      stubPlanSize();
      renderPlan();
      const before = viewBox();
      press(screen.getByRole("group", { name: "Floor plan of Ground" }), -80, 0, { ...MOUSE, button: 2 });
      expect(viewBox()).toEqual(before);
    });

    it("keeps every location reachable and selectable from the keyboard", async () => {
      const user = userEvent.setup();
      const { onSelect } = renderPlan();
      screen.getByRole("button", { name: "A-01, Bin" }).focus();
      await user.keyboard("{Enter}");
      expect(onSelect).toHaveBeenLastCalledWith("bin-a");
      expect(screen.getByRole("button", { name: "A-01, Bin" }).getAttribute("tabindex")).toBe("0");
    });
  });

  describe("skipping the plan", () => {
    it("offers a skip link ahead of the plan's tab stops", () => {
      renderPlan();
      const skip = screen.getByRole("link", { name: "Skip the floor plan" });
      const plan = screen.getByRole("group", { name: "Floor plan of Ground" });
      const first = screen.getByRole("button", { name: "RACK-1, Rack" });
      expect(skip.compareDocumentPosition(plan) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(skip.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it("moves focus past every location to a note that says the plan has ended, without changing the address", async () => {
      const user = userEvent.setup();
      renderPlan();
      const hash = window.location.hash;
      await user.click(screen.getByRole("link", { name: "Skip the floor plan" }));
      expect(document.activeElement?.textContent).toBe("End of the floor plan · 3 locations");
      expect(document.activeElement?.closest("svg")).toBeNull();
      expect(window.location.hash).toBe(hash);
    });

    it("is reached by Tab after the zoom controls and before the plan", async () => {
      const user = userEvent.setup();
      renderPlan();
      await user.tab();
      expect(document.activeElement?.getAttribute("aria-label")).toBe("Zoom in");
      await user.tab();
      await user.tab();
      await user.tab();
      expect(document.activeElement?.textContent).toBe("Skip the floor plan");
      await user.tab();
      expect(document.activeElement?.getAttribute("aria-label")).toBe("Floor plan of Ground");
    });
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
