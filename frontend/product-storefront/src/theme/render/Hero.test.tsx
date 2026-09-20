/**
 * The hero's type scale, which used to be measured against the browser window.
 *
 * `vw` is the viewport; a storefront canvas is a fixed-width box — 390px in the editor's phone
 * preview, a scaled 1280 in the gallery's miniature. A heading sized in `vw` therefore rendered at
 * full desktop size inside a phone frame, and the preview lied about what a buyer would see. This
 * pins the replacement: the ramp reads the `width` prop the renderers already thread.
 *
 * The declaration is a `clamp()` string this code builds, which is exactly the kind of layout fact
 * jsdom *can* check. What the browser had to answer — that the computed size really does come out
 * at 32px on a 390 canvas and 54.4px on a 1280 one — was checked there.
 */

import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { CATEGORY_LIST, PRODUCTS } from "@/api/fixtures";
import { STACK_WIDTH } from "../schema";
import type { SectionInstance, SettingValue } from "../schema";
import { SectionRenderer } from "./SectionRenderer";

const PHONE = 390;
const TABLET = 834;
const DESKTOP = 1280;

function renderHero(width: number, settings: Record<string, SettingValue> = {}) {
  const section: SectionInstance = {
    id: "hero",
    type: "hero",
    settings: { heading: "Fasteners, fittings and fixings", ...settings },
    layout: { x: 0, y: 0, w: 12, h: 10 },
  };
  const view = render(
    <MemoryRouter>
      <SectionRenderer
        section={section}
        products={PRODUCTS}
        categories={CATEGORY_LIST}
        stacked={width < STACK_WIDTH}
        width={width}
        mode="trade"
      />
    </MemoryRouter>,
  );
  const heading = view.container.querySelector("h1");
  if (!heading) throw new Error("the hero rendered no h1");
  return { view, fontSize: heading.style.fontSize };
}

describe("the hero heading's size", () => {
  it("is measured against the canvas, never the viewport", () => {
    const { fontSize } = renderHero(DESKTOP);
    expect(fontSize).not.toContain("vw");
    expect(fontSize).toContain("px");
  });

  it("scales with the width prop rather than staying put as the canvas narrows", () => {
    const phone = renderHero(PHONE);
    const tablet = renderHero(TABLET);
    const desktop = renderHero(DESKTOP);

    // 4.4% of the canvas, which is what `4.4vw` resolved to when the store filled the window.
    expect(phone.fontSize).toBe("clamp(2rem, 17.2px, 3.4rem)");
    expect(tablet.fontSize).toBe("clamp(2rem, 36.7px, 3.4rem)");
    expect(desktop.fontSize).toBe("clamp(2rem, 56.3px, 3.4rem)");
  });

  it("keeps the floor and the ceiling in rem, so a reader's own font size still moves it", () => {
    // A px ramp with px bounds would freeze the heading at 32px for someone browsing at 24px
    // default. Only the fluid middle term is computed.
    const { fontSize } = renderHero(PHONE);
    expect(fontSize.startsWith("clamp(2rem,")).toBe(true);
    expect(fontSize.endsWith("3.4rem)")).toBe(true);
  });

  it("is the same ramp in the overlay arrangement, which is the one a photograph sits behind", () => {
    const split = renderHero(DESKTOP, { layout: "split" });
    const overlay = renderHero(DESKTOP, { layout: "overlay" });
    expect(overlay.fontSize).toBe(split.fontSize);
  });

  it("falls back to the floor rather than to nothing when the canvas has not been measured", () => {
    const { fontSize } = renderHero(0);
    expect(fontSize).toBe("clamp(2rem, 0px, 3.4rem)");
  });
});
