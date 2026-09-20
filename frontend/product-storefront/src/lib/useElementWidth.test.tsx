import { useRef, useState } from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useElementWidth } from "./useElementWidth";

/**
 * **jsdom has no layout and no `ResizeObserver`**, so these tests install a stub observer and drive
 * it by hand. What they pin is the wiring: that the hook attaches to an element which only appears
 * on a later render, that a size reported after that first measurement still reaches the returned
 * width, and that unmount disconnects. What they cannot pin is the measurement itself —
 * `getBoundingClientRect` is 0 for everything here, so "the number equals the element's real width"
 * is only ever established in a browser, and was, at 390 and at 1280 in both directions.
 */

interface Stub {
  /** What a freshly observed element reports, the way a real observer's first delivery does. */
  width: number;
  /** Elements currently observed, oldest first. */
  observed: HTMLElement[];
  disconnects: number;
  /** Report `width` again to every live observer, the way a resize would. */
  resize: (width: number) => void;
}

const REAL_RESIZE_OBSERVER = globalThis.ResizeObserver;

function entryFor(target: HTMLElement, width: number): ResizeObserverEntry {
  return { target, contentRect: { width } } as unknown as ResizeObserverEntry;
}

function installObserverStub(width: number): Stub {
  const live = new Map<ResizeObserverCallback, HTMLElement[]>();
  const stub: Stub = {
    width,
    observed: [],
    disconnects: 0,
    resize(next) {
      stub.width = next;
      for (const [callback, elements] of live) {
        const entries = elements.map((target) => entryFor(target, next));
        if (entries.length > 0) callback(entries, {} as ResizeObserver);
      }
    },
  };

  class StubResizeObserver implements ResizeObserver {
    constructor(private readonly callback: ResizeObserverCallback) {
      live.set(callback, []);
    }
    observe(target: Element) {
      const element = target as HTMLElement;
      stub.observed.push(element);
      live.get(this.callback)?.push(element);
      // A real observer delivers the element's current size as soon as it is observed. The hook
      // depends on that for its first measurement, so the stub has to do it too.
      this.callback([entryFor(element, stub.width)], this);
    }
    unobserve() {
      /* the hook disconnects rather than unobserving */
    }
    disconnect() {
      stub.disconnects += 1;
      live.delete(this.callback);
    }
  }

  globalThis.ResizeObserver = StubResizeObserver as unknown as typeof ResizeObserver;
  return stub;
}

afterEach(() => {
  globalThis.ResizeObserver = REAL_RESIZE_OBSERVER;
});

/** The shape every caller has: the measured element is behind an async state, so it mounts late. */
function Probe({ fallback }: { fallback: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const width = useElementWidth(ref, fallback);
  return (
    <div>
      <output data-testid="width">{width}</output>
      <button type="button" onClick={() => setReady(true)}>
        Load
      </button>
      {ready ? <div ref={ref} data-testid="frame" /> : <p>Loading</p>}
    </div>
  );
}

function widthNow(): number {
  return Number(screen.getByTestId("width").textContent);
}

function load(): void {
  act(() => screen.getByRole("button", { name: "Load" }).click());
}

describe("useElementWidth", () => {
  it("measures an element that only exists after a later render", () => {
    const stub = installObserverStub(390);
    render(<Probe fallback={1200} />);

    // Nothing to observe on the first render, so the caller gets the fallback it reserved space at.
    expect(stub.observed).toHaveLength(0);
    expect(widthNow()).toBe(1200);

    load();

    expect(stub.observed).toEqual([screen.getByTestId("frame")]);
    expect(widthNow()).toBe(390);
  });

  it("tracks a resize after the first measurement", () => {
    const stub = installObserverStub(1280);
    render(<Probe fallback={1280} />);
    load();
    expect(widthNow()).toBe(1280);

    // The case the frozen width hid: a page loaded wide and then narrowed has to end up narrow.
    act(() => stub.resize(390));

    expect(widthNow()).toBe(390);
  });

  it("disconnects its observer when the caller unmounts", () => {
    const stub = installObserverStub(390);
    const view = render(<Probe fallback={1200} />);
    load();
    const before = stub.disconnects;

    view.unmount();

    expect(stub.disconnects).toBeGreaterThan(before);
    // Nothing is observed any more, so a later resize reaches no setState on an unmounted tree.
    act(() => stub.resize(800));
    expect(stub.observed.every((element) => !element.isConnected)).toBe(true);
  });

  it("keeps the fallback where there is no ResizeObserver at all", () => {
    globalThis.ResizeObserver = undefined as unknown as typeof ResizeObserver;
    render(<Probe fallback={1200} />);
    load();

    // jsdom reports every element as 0 wide, so measuring here would be worse than not measuring.
    expect(widthNow()).toBe(1200);
  });
});
