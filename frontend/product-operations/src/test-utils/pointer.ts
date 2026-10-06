/**
 * The browser pieces a pointer-driven canvas needs and jsdom does not have: `PointerEvent` (so
 * `fireEvent.pointerDown(el, { pointerType: "touch" })` carries its pointer type and position),
 * `setPointerCapture`, the SVG screen-to-canvas mapping, and `ResizeObserver`. The mapping is the
 * identity — a client coordinate is a canvas coordinate — which is all a test of *what a gesture
 * does* needs. Undo with `vi.unstubAllGlobals()` and `uninstallCanvasPointerSupport()`.
 */

import { vi } from "vitest";

class TestPointerEvent extends MouseEvent {
  readonly pointerId: number;
  readonly pointerType: string;

  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
    this.pointerType = init.pointerType ?? "mouse";
  }
}

const PROTOTYPE_STUBS: ReadonlyArray<[object, string, unknown]> = [
  [Element.prototype, "setPointerCapture", () => undefined],
  [Element.prototype, "releasePointerCapture", () => undefined],
  [
    SVGSVGElement.prototype,
    "createSVGPoint",
    () => {
      const point = { x: 0, y: 0, matrixTransform: () => ({ x: point.x, y: point.y }) };
      return point;
    },
  ],
  [SVGSVGElement.prototype, "getScreenCTM", () => ({ inverse: () => ({}) })],
];

export function installCanvasPointerSupport(): void {
  vi.stubGlobal("PointerEvent", TestPointerEvent);
  for (const [target, name, value] of PROTOTYPE_STUBS) {
    Object.defineProperty(target, name, { configurable: true, value });
  }
}

export function uninstallCanvasPointerSupport(): void {
  for (const [target, name] of PROTOTYPE_STUBS) Reflect.deleteProperty(target, name);
}

/** Every element a test observes reports `width` x `height` CSS pixels, as a browser lays it out. */
export function stubElementSize(width: number, height: number): void {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(private readonly callback: ResizeObserverCallback) {}
      observe(target: Element) {
        this.callback([{ target, contentRect: { width, height } } as ResizeObserverEntry], this as unknown as ResizeObserver);
      }
      unobserve() {}
      disconnect() {}
    },
  );
}

/** `(any-pointer: coarse)` matches — a phone or tablet. */
export function stubCoarsePointer(): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("coarse"),
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}
