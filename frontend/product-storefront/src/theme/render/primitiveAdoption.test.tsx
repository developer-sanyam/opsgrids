/**
 * Three capabilities that existed in `primitives.tsx` and were unreachable from a section.
 *
 * Each was built correctly and then stranded: the enabling edit sat in a file the unit that added
 * the primitive did not own, so the option was exported, typed, documented — and never selected by
 * anything. A primitive nobody can reach is indistinguishable from a primitive nobody wrote, and
 * the only thing that tells the two apart is a test that reaches for it.
 *
 * The library-wide `vw` sweep at the bottom is the one worth keeping forever. Four separate
 * viewport-relative sizes have been fixed in this build and every one of them was invisible in
 * jsdom, because jsdom has no layout engine and a `clamp()` containing `vw` is a perfectly
 * well-formed string. What this checks is not the layout — it is the *declaration*, which is a
 * string this code computes, and that is exactly the part a browser cannot be asked about cheaply.
 */

import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { RETAIL_CATEGORY_LIST, RETAIL_PRODUCTS } from "@/api/fixtures";
import { SECTION_DEFS, defaultSettings, sectionDef } from "../sections";
import type { SectionInstance, SettingValue } from "../schema";
import { SectionRenderer } from "./SectionRenderer";

const PHONE = 390;
const DESKTOP = 1280;

function renderSection(
  type: string,
  settings: Record<string, SettingValue> = {},
  width: number = DESKTOP,
  mode: "retail" | "trade" = "retail",
) {
  const section: SectionInstance = {
    id: `test-${type}`,
    type,
    settings: { ...defaultSettings(type), ...settings },
    layout: { x: 0, y: 0, w: 12, h: 8 },
  };
  return render(
    <MemoryRouter>
      <SectionRenderer
        section={section}
        products={RETAIL_PRODUCTS}
        categories={RETAIL_CATEGORY_LIST}
        stacked={width < 768}
        width={width}
        mode={mode}
      />
    </MemoryRouter>,
  );
}

/* ------------------------- (a) the display step --------------------------- */

/**
 * `HeadingSize` grew a fifth step above `xl`, for the closing wordmark the furniture reference ends
 * on. `xl` bottoms out at 2rem, so on a real 390 canvas that block came out at 32px — a section
 * title, not a name across the foot of a page.
 */
/**
 * One prose block's heading size, rendered and thrown away.
 *
 * It unmounts before returning because these read a size per option in a loop, and RTL's queries
 * are bound to `document.body` rather than to the container — leave the previous render standing
 * and the second call finds two headings and fails on the ambiguity rather than on the size.
 */
function proseSize(size: string, width: number): string {
  const view = renderSection("rich-text", { size, heading: `size ${size}` }, width);
  const fontSize = view.container.querySelector("h2")?.style.fontSize ?? "";
  view.unmount();
  return fontSize;
}

describe("the display heading step", () => {
  it("is offered by the editor and accepted by the renderer, which is two files agreeing", () => {
    const options = sectionDef("rich-text")?.fields.find((field) => field.key === "size")?.options;
    const offered = (options ?? []).map((option) => option.value);

    expect(offered).toContain("display");

    /* The invariant, not just the new entry: an option the renderer declines is a control that
       silently does nothing, and `choice()` declines by falling back to `lg` without a word. Every
       value the editor offers has to round-trip to a distinct size. */
    const sizes = offered.map((value) => proseSize(String(value), DESKTOP));
    expect(sizes.every((size) => size !== "")).toBe(true);
    expect(new Set(sizes).size, `rich-text sizes: ${offered.join(", ")} → ${sizes.join(", ")}`).toBe(
      offered.length,
    );
  });

  it("really is a step above xl, at the width the wordmark is judged at", () => {
    // Both clamp to their floor on a phone, which is the whole point: 2rem is 32px and 2.75rem
    // is 44px, and the reference's closing block is the larger one.
    expect(proseSize("xl", PHONE)).toContain("2rem");
    expect(proseSize("display", PHONE)).toContain("2.75rem");
  });

  it("lifts the prose under it, so a wordmark is not followed by copy three steps down", () => {
    const { container } = renderSection(
      "rich-text",
      { size: "display", heading: "opsgrids", body: "Hand-made in Jodhpur." },
      DESKTOP,
    );
    expect(container.querySelector("p")?.style.fontSize).toBe("1.05rem");
  });
});

/* ---------------------------- (b) tone="inherit" -------------------------- */

/**
 * An outline action on a ground the theme's page-level pair was never chosen against.
 *
 * The failure is specifically the **border**. The older workaround recoloured the label with an
 * inner `<span>`, which could only ever reach the words; the edge went on being `T.border` — a
 * token drawn to separate a surface from the page — on top of a photograph. On the dark furniture
 * theme that border is a near-black brown on a scrim, which is no border at all.
 */
describe("an outline action on a band that paints its own ground", () => {
  function secondary(settings: Record<string, SettingValue>) {
    const view = renderSection(
      "hero",
      { heading: "Made by hand", secondaryLabel: "See the collection", ...settings },
      DESKTOP,
      "trade",
    );
    const link = view.getByRole("link", { name: "See the collection" });
    return { link, view };
  }

  it("takes the band's foreground for its words and its edge, over a photograph", () => {
    const { link } = secondary({ layout: "overlay", tone: "surface" });
    expect(link.style.color).toBe("inherit");
    expect(link.style.border).toContain("currentcolor");
    expect(link.style.border).not.toContain("--st-border");
  });

  it("does the same on an accent ground, where T.text is the wrong end of the pair", () => {
    const { link } = secondary({ layout: "split", tone: "accent" });
    expect(link.style.color).toBe("inherit");
    expect(link.style.border).toContain("currentcolor");
  });

  it("keeps the theme's own pair on the theme's own surface, so nothing shipped moves", () => {
    const { link } = secondary({ layout: "split", tone: "surface" });
    expect(link.style.color).toBe("var(--st-text)");
    expect(link.style.border).toContain("var(--st-border)");
  });

  it("no longer wraps the label in a span that recolours it, which the tone now covers", () => {
    const { link } = secondary({ layout: "overlay", tone: "surface" });
    expect(link.querySelector("span")).toBeNull();
    expect(link.textContent).toBe("See the collection");
  });

  /* The solid action is deliberately untouched by any of this: it paints `T.accent` behind
     `T.accentText`, a pair the theme guarantees wherever it lands — including on a colour a
     merchant typed into a banner's tint. `tone` is inert on it by design. */
  it("leaves the solid call to action carrying its own pair", () => {
    const view = renderSection(
      "hero",
      { heading: "Made by hand", ctaLabel: "Shop now" },
      DESKTOP,
      "trade",
    );
    const cta = view.getByRole("link", { name: "Shop now" });
    expect(cta.style.background).toBe("var(--st-accent)");
    expect(cta.style.color).toBe("var(--st-accent-text)");
  });
});

/* -------------------------- (c) the last vw call site --------------------- */

/**
 * The sweep.
 *
 * `vw` is the browser viewport, and **nothing in this app renders at the browser viewport**: the
 * editor composes at 390 or 1280 inside a window several times that, and the gallery composes at
 * 1280 and scales the result down. So a heading sized in `vw` reads at the size of the machine the
 * preview is running on, which is the one number a merchant checking their phone layout must not
 * be shown.
 *
 * Every fluid heading now takes `width`. This asserts that for the whole library at once rather
 * than for the rail that happened to be last, because the next section someone writes will forget
 * the prop and a per-file test would not be there to say so.
 */
describe("no section in the library sizes type against the viewport", () => {
  it.each(SECTION_DEFS.map((def) => def.type))("%s declares no vw font-size", (type) => {
    for (const width of [PHONE, DESKTOP]) {
      const { container, unmount } = renderSection(type, {}, width);
      const offenders = [...container.querySelectorAll<HTMLElement>("*")]
        .filter((element) => element.style.fontSize.includes("vw"))
        .map((element) => `<${element.tagName.toLowerCase()}> ${element.style.fontSize}`);

      expect(
        offenders,
        `${type} at ${width}px sizes type against the window, not the canvas:\n  ${offenders.join(
          "\n  ",
        )}\n  Pass width={width} to StoreHeading.`,
      ).toEqual([]);
      unmount();
    }
  });

  it("moves a rail heading with the canvas, which is what the prop buys", () => {
    const railHeading = (width: number) => {
      const view = renderSection("product-carousel", { heading: "Moving this week" }, width);
      const fontSize = view.container.querySelector("h2")?.style.fontSize ?? "";
      view.unmount();
      return fontSize;
    };

    // Stacked picks the fixed `md` step; the desktop canvas picks the fluid `lg` ramp, resolved
    // against 1280 rather than against whatever window the test runner happens to have.
    expect(railHeading(PHONE)).toBe("1.25rem");
    expect(railHeading(DESKTOP)).toBe("clamp(1.5rem, 33.3px, 2.1rem)");
  });
});
