import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PageDoc, SectionDef, ThemeTokens } from "@/theme/schema";

import type { Device } from "./document";

import { Canvas } from "./Canvas";
import { SECTION_DRAG_MIME, sectionDragHint } from "./dragTypes";

/**
 * The pointer half of the canvas, which the keyboard tests cannot reach.
 *
 * **What this file cannot tell you.** jsdom performs no layout: every element measures zero and
 * every rect here is a stub this file writes. It therefore cannot see anything that depends on how
 * wide a pane *really* is — and it did not: the editor shipped with `stacked` derived from the
 * measured pane width, and because jsdom reports zero (which the old guard read as "not narrow")
 * every test below passed while layout editing was disabled for every real user at every window
 * size. That was found by driving Chrome over CDP, and it is the only way this class of defect is
 * found. Treat the numbers below as arithmetic coverage, not as evidence the editor works.
 *
 * What it can pin honestly is the arithmetic and the decisions that are pure functions of props:
 * that stacking follows the *previewed* width rather than the pane's, that a pane narrower than
 * the buyer breakpoint still edits, and that pointer positions are divided by the canvas scale.
 * The 430px stub in the last block is the width Chrome actually measured for this pane at a
 * 1440px viewport.
 */

const BLOCK: SectionDef = {
  type: "test-block",
  name: "Test block",
  group: "content",
  defaultLayout: { w: 6, h: 4 },
  fields: [],
};

const TOKENS: ThemeTokens = {
  colorBg: "#ffffff",
  colorSurface: "#f7f7f7",
  colorText: "#121212",
  colorMuted: "#666666",
  colorAccent: "#1d4ed8",
  colorAccentText: "#ffffff",
  colorBorder: "#e3e3e3",
  fontHeading: "Inter, sans-serif",
  fontBody: "Inter, sans-serif",
  radius: 8,
  space: 16,
  maxWidth: 1200,
};

const PAGE: PageDoc = {
  id: "home",
  route: "/",
  name: "Home",
  sections: [
    { id: "s1", type: "test-block", settings: {}, layout: { x: 0, y: 0, w: 6, h: 4 } },
  ],
};

const CANVAS_WIDTH = 1420;

function renderCanvas(
  options: { selectedId?: string | null; device?: Device } = {},
) {
  const handlers = {
    onSelect: vi.fn(),
    onMove: vi.fn(),
    onResize: vi.fn(),
    onNudge: vi.fn(),
    onScale: vi.fn(),
    onDepth: vi.fn(),
    onRemove: vi.fn(),
    onDragEnd: vi.fn(),
    onDropSection: vi.fn(),
  };
  const view = render(
    <Canvas
      page={PAGE}
      library={[BLOCK]}
      tokens={TOKENS}
      brand={{ name: "Verma Traders", tagline: "Since 1994", logoText: "VT" }}
      mode="trade"
      products={[]}
      categories={[]}
      device={options.device ?? "desktop"}
      selectedId={options.selectedId === undefined ? "s1" : options.selectedId}
      canEdit
      focusRequest={null}
      {...handlers}
    />,
  );
  return { ...handlers, container: view.container };
}

function frame(): HTMLElement {
  return screen.getByRole("button", { name: /^Test block, column/ });
}

/** The section's grid placement — `1 / -1` is the stacked, full-width form. */
function placement(container: HTMLElement): string {
  const item = container.querySelector("li");
  if (!(item instanceof HTMLElement)) throw new Error("No section on the canvas");
  return item.style.gridColumn;
}

/** The pointer-only resize grips, which only exist when the layout is editable. */
function handles(container: HTMLElement): NodeListOf<Element> {
  return container.querySelectorAll("li span[aria-hidden]");
}

/**
 * jsdom implements no `PointerEvent`, and Testing Library's fallback drops `clientX`, `button` and
 * everything else the canvas actually reads. A `MouseEvent` carries all of them and React maps it
 * to `onPointerDown`/`onPointerMove` by type, which is exactly what the browser does.
 */
function pointer(
  target: Element,
  type: "pointerdown" | "pointermove" | "pointerup",
  init: MouseEventInit,
) {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, ...init });
  Object.defineProperty(event, "pointerId", { value: 1 });
  fireEvent(target, event);
}

/** The same trick for a drop, whose `DragEvent` jsdom also does not construct. */
function drop(target: Element, init: MouseEventInit, dataTransfer: unknown) {
  const event = new MouseEvent("drop", { bubbles: true, cancelable: true, ...init });
  Object.defineProperty(event, "dataTransfer", { value: dataTransfer });
  fireEvent(target, event);
}

/** Stands in for layout: the width every element reports, since jsdom computes none. */
function stubLayout(width = CANVAS_WIDTH) {
  Element.prototype.getBoundingClientRect = function rect(this: Element): DOMRect {
    return {
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: width,
      bottom: 800,
      width,
      height: 800,
      toJSON: () => ({}),
    } as DOMRect;
  };
}

const originalRect = Element.prototype.getBoundingClientRect;

beforeEach(() => {
  stubLayout();
  // Pointer capture is how a drag keeps receiving moves once the pointer leaves the section;
  // jsdom implements none of the pointer-capture API.
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
});

afterEach(() => {
  Element.prototype.getBoundingClientRect = originalRect;
});

describe("dragging a section with a pointer", () => {
  it("moves it by whole grid cells, not pixels", () => {
    const { onMove, onDragEnd } = renderCanvas();

    pointer(frame(), "pointerdown", { button: 0, clientX: 100, clientY: 100 });
    // Two column steps right (2 × 119.67) and two row steps down (2 × 56).
    pointer(frame(), "pointermove", { clientX: 100 + 239, clientY: 100 + 112 });

    expect(onMove).toHaveBeenLastCalledWith("s1", 2, 2);

    pointer(frame(), "pointerup", {});
    expect(onDragEnd).toHaveBeenCalledTimes(1);
  });

  it("ignores a movement smaller than half a cell, so a click is not a move", () => {
    const { onMove } = renderCanvas();

    pointer(frame(), "pointerdown", { button: 0, clientX: 100, clientY: 100 });
    pointer(frame(), "pointermove", { clientX: 140, clientY: 110 });

    expect(onMove).toHaveBeenLastCalledWith("s1", 0, 0);
  });

  it("leaves focus on the section, so the keyboard can carry on from where the mouse stopped", () => {
    // `preventDefault` on `pointerdown` suppresses the focus `mousedown` would have moved, which
    // Chrome showed leaving `document.activeElement` on `<body>` after a click. The canvas moves
    // focus itself instead; this pins that it still does.
    renderCanvas();

    pointer(frame(), "pointerdown", { button: 0, clientX: 100, clientY: 100 });

    expect(document.activeElement).toBe(frame());
  });

  it("does not start a drag from the secondary mouse button", () => {
    const { onMove } = renderCanvas();

    pointer(frame(), "pointerdown", { button: 2, clientX: 100, clientY: 100 });
    pointer(frame(), "pointermove", { clientX: 400, clientY: 300 });

    expect(onMove).not.toHaveBeenCalled();
  });

  it("resizes from the corner handle in the same units", () => {
    const { container, onResize } = renderCanvas();
    // The handles carry no accessible name on purpose — everything they do is on the section
    // itself under Shift and an arrow key — so the test reaches for them by position.
    const grips = handles(container);
    const corner = grips[grips.length - 1];
    expect(corner).toBeDefined();

    pointer(corner as Element, "pointerdown", { button: 0, clientX: 700, clientY: 200 });
    pointer(corner as Element, "pointermove", { clientX: 700 + 239, clientY: 200 + 56 });

    expect(onResize).toHaveBeenLastCalledWith("s1", 8, 5);
  });
});

describe("a pane narrower than the buyer's stacking breakpoint", () => {
  // 430px is what Chrome measured for this pane at a 1440px viewport, inside the app shell's
  // 1600px cap with the product rail and both editor panes open. The editor used to read that as
  // "the canvas is narrow, so stack and stop editing", which disabled layout editing outright.
  const PANE = 430;
  const SCALE = PANE / 1280;

  it("still lays the page out in twelve columns and still edits", () => {
    stubLayout(PANE);
    const { container } = renderCanvas();

    expect(placement(container)).toBe("1 / span 6");
    expect(handles(container).length).toBe(3);
  });

  it("resolves a pointer position through the canvas scale", () => {
    stubLayout(PANE);
    const { onMove } = renderCanvas();

    pointer(frame(), "pointerdown", { button: 0, clientX: 50, clientY: 50 });
    // Two columns and one row, in the *scaled* pixels the pointer actually travels.
    pointer(frame(), "pointermove", {
      clientX: 50 + Math.round(2 * 108 * SCALE),
      clientY: 50 + Math.round(56 * SCALE),
    });

    expect(onMove).toHaveBeenLastCalledWith("s1", 2, 1);
  });
});

describe("the phone preset", () => {
  it("stacks, stops offering layout edits, and says why", () => {
    const { container, onMove } = renderCanvas({ device: "mobile" });

    expect(placement(container)).toBe("1 / -1");
    expect(handles(container).length).toBe(0);
    expect(screen.getByText("You're editing the phone width")).toBeDefined();

    pointer(frame(), "pointerdown", { button: 0, clientX: 100, clientY: 100 });
    pointer(frame(), "pointermove", { clientX: 400, clientY: 300 });
    expect(onMove).not.toHaveBeenCalled();
  });

  it("is the preview width that decides that, not the pane", () => {
    // The same 430px pane that must keep editing on the desktop preset.
    stubLayout(430);
    const { container } = renderCanvas({ device: "tablet" });

    expect(placement(container)).toBe("1 / span 6");
    expect(screen.queryByText("You're editing the phone width")).toBeNull();
  });
});

describe("dropping a section from the palette", () => {
  it("adds the dragged type at the cell it was dropped on", () => {
    const { onDropSection, container } = renderCanvas({ selectedId: null });
    const grid = container.querySelector("[data-storefront-frame] > div:last-child");
    expect(grid).toBeDefined();

    drop(grid as Element, { clientX: 600, clientY: 120 }, {
      getData: () => BLOCK.type,
      types: [SECTION_DRAG_MIME, sectionDragHint(BLOCK.type)],
    });

    expect(onDropSection).toHaveBeenCalledWith(BLOCK, { x: 5, y: 2 });
  });

  it("ignores a drag carrying anything that is not a section of this theme", () => {
    const { onDropSection, container } = renderCanvas({ selectedId: null });
    const grid = container.querySelector("[data-storefront-frame] > div:last-child");

    drop(grid as Element, { clientX: 600, clientY: 120 }, {
      getData: () => "not-a-section",
      types: [SECTION_DRAG_MIME],
    });

    expect(onDropSection).not.toHaveBeenCalled();
  });
});
