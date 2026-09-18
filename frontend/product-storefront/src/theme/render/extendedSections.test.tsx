/**
 * The six sections that grew retail arrangements: what they do at a phone width, and what they
 * still do at a desktop one.
 *
 * These pin the two things a jsdom suite genuinely can check about layout — the column count a
 * section asks for, and the track a scrolling row declares — because both are strings this code
 * computes rather than anything a layout engine decides. Whether 2.2 cards actually peek and
 * whether a rail actually scrolls are browser questions and are checked there.
 *
 * The regression that all six trade themes still render with no fallback and exactly one `h1` per
 * page is **not** repeated here: `StoreRenderer.test.tsx`'s `describe.each(THEMES)` sweep already
 * renders every page of every theme at 1200 and at 390 and asserts both.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { RETAIL_CATEGORY_LIST, RETAIL_PRODUCTS } from "@/api/fixtures";
import { defaultSettings } from "../sections";
import type { SectionInstance, SettingValue } from "../schema";
import { SectionRenderer } from "./SectionRenderer";

/** The phone the retail work is designed at, and the desktop canvas. */
const PHONE = 390;
const DESKTOP = 1200;

function renderSection(
  type: string,
  settings: Record<string, SettingValue>,
  width: number,
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

describe("category tiles on a phone", () => {
  it("takes its column count from mobileColumns below the stacking width and columns above it", () => {
    const settings = { columns: 3, mobileColumns: 4 };

    const phone = renderSection("category-tiles", settings, PHONE);
    expect(phone.container.querySelector("ul")?.style.gridTemplateColumns).toBe(
      "repeat(4, minmax(0, 1fr))",
    );
    phone.unmount();

    const desktop = renderSection("category-tiles", settings, DESKTOP);
    expect(desktop.container.querySelector("ul")?.style.gridTemplateColumns).toBe(
      "repeat(3, minmax(0, 1fr))",
    );
  });

  it("never forces a single column on a phone, which is the bug it was fixing", () => {
    const { container } = renderSection("category-tiles", {}, PHONE);
    expect(container.querySelector("ul")?.style.gridTemplateColumns).toBe(
      "repeat(4, minmax(0, 1fr))",
    );
  });

  it("clamps the column count to the declared range at both ends", () => {
    const wide = renderSection("category-tiles", { columns: 99 }, DESKTOP);
    expect(wide.container.querySelector("ul")?.style.gridTemplateColumns).toBe(
      "repeat(6, minmax(0, 1fr))",
    );
    wide.unmount();

    const narrow = renderSection("category-tiles", { columns: 1 }, DESKTOP);
    expect(narrow.container.querySelector("ul")?.style.gridTemplateColumns).toBe(
      "repeat(2, minmax(0, 1fr))",
    );
  });

  it("keeps the list style at one column whatever the column settings say", () => {
    const { container } = renderSection(
      "category-tiles",
      { style: "list", columns: 5, mobileColumns: 4 },
      PHONE,
    );
    expect(container.querySelector("ul")?.style.gridTemplateColumns).toBe(
      "repeat(1, minmax(0, 1fr))",
    );
  });

  it("draws a picture per tile in the photograph and compact styles, and none in the word styles", () => {
    for (const style of ["photo", "compact"]) {
      const view = renderSection("category-tiles", { style, limit: 4 }, PHONE);
      expect(view.container.querySelectorAll("li img, li svg").length).toBe(4);
      view.unmount();
    }

    for (const style of ["tile", "list"]) {
      const view = renderSection("category-tiles", { style, limit: 4 }, PHONE);
      expect(view.container.querySelectorAll("li img, li svg").length).toBe(0);
      view.unmount();
    }
  });

  it("holds a tile's picture to its chosen shape and offers nothing wider than 16:9", () => {
    const { container } = renderSection(
      "category-tiles",
      { style: "photo", aspect: "square", limit: 2 },
      PHONE,
    );
    const plate = container.querySelector<HTMLElement>("li img, li svg");
    expect(plate?.style.aspectRatio).toBe("1 / 1");

    const wider = renderSection(
      "category-tiles",
      { style: "photo", aspect: "cinemascope", limit: 2 },
      PHONE,
    );
    // An unknown shape falls back to the 4:3 the bundled art is actually drawn at.
    expect(
      wider.container.querySelector<HTMLElement>("li img, li svg")?.style.aspectRatio,
    ).toBe("4 / 3");
  });

  it("takes each tile's picture from the category record, not from a numbered settings key", () => {
    const withArt = RETAIL_CATEGORY_LIST.filter((category) => category.image_url !== null);
    if (withArt.length === 0) throw new Error("the fixture catalogue should carry category art");

    const { container } = renderSection("category-tiles", { style: "photo", limit: 12 }, DESKTOP);
    const sources = [...container.querySelectorAll("li img")].map((img) => img.getAttribute("src"));
    for (const category of withArt.slice(0, 12)) {
      expect(sources).toContain(category.image_url);
    }
  });
});

describe("a product grid on a phone", () => {
  it("takes its column count from mobileColumns below the stacking width and columns above it", () => {
    const settings = { columns: 4, mobileColumns: 2 };

    const phone = renderSection("product-grid", settings, PHONE);
    expect(phone.container.querySelector<HTMLElement>("section > div")?.style.gridTemplateColumns)
      .toBe("repeat(2, minmax(0, 1fr))");
    phone.unmount();

    const desktop = renderSection("product-grid", settings, DESKTOP);
    expect(desktop.container.querySelector<HTMLElement>("section > div")?.style.gridTemplateColumns)
      .toBe("repeat(4, minmax(0, 1fr))");
  });

  it("still lets a merchant ask for one card per row on a phone", () => {
    const { container } = renderSection("product-grid", { mobileColumns: 1 }, PHONE);
    expect(container.querySelector<HTMLElement>("section > div")?.style.gridTemplateColumns).toBe(
      "repeat(1, minmax(0, 1fr))",
    );
  });
});

describe("the product rail", () => {
  it("holds a card to about half the canvas on a phone, so the next one peeks", () => {
    const { container } = renderSection("product-carousel", { cardWidth: 230 }, PHONE);
    // 42% of a 390px canvas — verified in Chrome to leave a fifth of the third card showing
    // against the content track the cards actually sit in. Measured against the canvas, never the
    // viewport: the editor's canvas is a 390px box inside a far wider window.
    expect(container.querySelector<HTMLElement>("ul")?.style.gridAutoColumns).toBe(
      "minmax(min(230px, 164px), 1fr)",
    );
  });

  it("gives a card its full width at a desktop canvas", () => {
    const { container } = renderSection("product-carousel", { cardWidth: 260 }, DESKTOP);
    expect(container.querySelector<HTMLElement>("ul")?.style.gridAutoColumns).toBe(
      "minmax(260px, 1fr)",
    );
  });

  it("clamps a card width the editor let a merchant over-type", () => {
    const { container } = renderSection("product-carousel", { cardWidth: 4000 }, DESKTOP);
    expect(container.querySelector<HTMLElement>("ul")?.style.gridAutoColumns).toBe(
      "minmax(420px, 1fr)",
    );
  });

  it("stays a scrollable list reachable by keyboard, with no arrow buttons", () => {
    const { container } = renderSection("product-carousel", {}, PHONE);
    const track = container.querySelector<HTMLElement>("ul");
    expect(track?.tabIndex).toBe(0);
    expect(track?.getAttribute("aria-label")).toContain("scrollable list");
    // The only buttons in a rail belong to the cards. No arrows, and no timer moving content
    // under a reader: the file's own argument, and neither reference contradicts it.
    const chrome = [...container.querySelectorAll("button")].filter(
      (button) => button.closest("article") === null,
    );
    expect(chrome).toHaveLength(0);
  });

  it("moves the trailing link below the row when asked, keeping one link and its words", () => {
    const trailing = renderSection("product-carousel", { ctaLabel: "See all" }, DESKTOP);
    expect(trailing.getAllByRole("link", { name: /See all/ })).toHaveLength(1);
    trailing.unmount();

    const below = renderSection(
      "product-carousel",
      { ctaLabel: "See all", ctaPlacement: "below" },
      DESKTOP,
    );
    expect(below.getAllByRole("link", { name: /See all/ })).toHaveLength(1);
    // Below the row means after it in the document, which is also the reading and tab order.
    const link = below.getByRole("link", { name: /See all/ });
    const track = below.container.querySelector("ul");
    if (!track) throw new Error("the rail should render a track");
    expect(track.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });
});

describe("a hero with words over the picture", () => {
  it("paints a scrim between the photograph and the heading, and keeps one h1", () => {
    const { container } = renderSection("hero", { layout: "overlay", overlay: "scrim" }, DESKTOP);

    const wash = container.querySelector<HTMLElement>("section > span[aria-hidden]");
    expect(wash).not.toBeNull();
    expect(wash?.style.background).not.toBe("");
    expect(wash?.style.opacity).not.toBe("");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("paints a gradient instead when asked, and nothing at all when told not to", () => {
    const gradient = renderSection("hero", { layout: "overlay", overlay: "gradient" }, DESKTOP);
    expect(
      gradient.container.querySelector<HTMLElement>("section > span[aria-hidden]")?.style
        .backgroundImage,
    ).toContain("linear-gradient");
    gradient.unmount();

    const bare = renderSection("hero", { layout: "overlay", overlay: "none" }, DESKTOP);
    expect(bare.container.querySelector("section > span[aria-hidden]")).toBeNull();
    expect(bare.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("honours the focal point and the minimum height, clamping what the merchant typed", () => {
    const { container } = renderSection(
      "hero",
      { layout: "overlay", imageFocus: "bottom", minHeight: 9999 },
      DESKTOP,
    );
    const section = container.querySelector<HTMLElement>("section");
    expect(section?.style.minHeight).toBe("720px");
    expect(container.querySelector<HTMLElement>("section > img, section > svg")?.style.objectPosition)
      .toBe("bottom");
  });

  it("leaves the side-by-side arrangement alone — no scrim, no minimum height", () => {
    const { container } = renderSection("hero", {}, DESKTOP);
    expect(container.querySelector("section > span[aria-hidden]")).toBeNull();
    expect(container.querySelector<HTMLElement>("section")?.style.minHeight).toBe("");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });
});

describe("the editorial band", () => {
  it("sets the copy over the picture in overlay, with a scrim and no second section", () => {
    const { container } = renderSection("image-with-text", { layout: "overlay" }, DESKTOP);
    expect(container.querySelectorAll("section")).toHaveLength(1);
    expect(container.querySelector("section > span[aria-hidden]")).not.toBeNull();
    expect(screen.getByRole("heading", { level: 2 })).not.toBeNull();
  });

  it("paints no ground of its own until a merchant tints it", () => {
    const plain = renderSection("image-with-text", {}, DESKTOP);
    expect(plain.container.querySelector<HTMLElement>("section")?.style.background).toBe("");
    plain.unmount();

    const tinted = renderSection("image-with-text", { tint: "#f6d155" }, DESKTOP);
    // jsdom normalises a hex colour to `rgb()`, so compare against what it stores.
    expect(tinted.container.querySelector<HTMLElement>("section")?.style.background).toBe(
      "rgb(246, 209, 85)",
    );
  });

  it("insets a full-width band's copy to the page's own content column", () => {
    const { container } = renderSection(
      "image-with-text",
      { bleed: true, tint: "#f6d155" },
      DESKTOP,
    );
    const inline = container.querySelector<HTMLElement>("section")?.style.paddingInline ?? "";
    expect(inline).toContain("max(");
    expect(inline).toContain("--st-max-width");
  });
});

describe("prose", () => {
  it("sets a display block at the top of the theme's heading scale", () => {
    const heading = renderSection("rich-text", { size: "xl", heading: "opsgrids" }, DESKTOP).getByRole(
      "heading",
      { level: 2 },
    );
    expect(heading.style.fontSize).toContain("clamp(2rem");
  });

  it("paints no ground and no padding by default, which is what every trade theme gets", () => {
    const { container } = renderSection("rich-text", {}, DESKTOP);
    const section = container.querySelector<HTMLElement>("section");
    expect(section?.style.background).toBe("");
    expect(section?.style.paddingInline).toBe("");
  });

  it("takes a ground when asked and puts the legible foreground on it", () => {
    const { container } = renderSection("rich-text", { tone: "accent" }, DESKTOP);
    const section = container.querySelector<HTMLElement>("section");
    expect(section?.style.background).toBe("var(--st-accent)");
    expect(section?.style.color).toBe("var(--st-accent-text)");
  });
});
