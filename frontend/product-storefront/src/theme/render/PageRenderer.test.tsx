/**
 * The 12-column canvas: placement above the breakpoint, full-width stacking in `y` order below it,
 * a page with no sections at all, and the full-bleed rails.
 *
 * **What this file cannot tell you about bleed.** jsdom has no layout engine: it echoes back the
 * `grid-column` string this component set and knows nothing about where the box lands. It cannot
 * see whether a bleeding section actually reaches the canvas edge, nor whether it escapes the
 * editor's 390px preview frame — the defect the rails exist to avoid. That is browser work, and
 * was done in Chrome against the store route, the gallery miniature and the editor canvas.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { CATEGORY_LIST, PRODUCTS } from "@/api/fixtures";
import { BLEED_SETTING, STACK_WIDTH } from "../schema";
import type { PageDoc, SectionInstance } from "../schema";
import { defaultSettings } from "../sections";
import { THEMES } from "../themes";
import { PageRenderer } from "./PageRenderer";

const PAGE: PageDoc = {
  id: "test-page",
  route: "/",
  name: "Test",
  sections: [
    {
      id: "second",
      type: "rich-text",
      settings: { ...defaultSettings("rich-text"), heading: "Second in reading order" },
      layout: { x: 6, y: 4, w: 6, h: 5 },
    },
    {
      id: "first",
      type: "rich-text",
      settings: { ...defaultSettings("rich-text"), heading: "First in reading order" },
      layout: { x: 0, y: 0, w: 8, h: 4 },
    },
  ],
};

function renderPage(page: PageDoc, width: number) {
  return render(
    <MemoryRouter>
      <PageRenderer
        page={page}
        products={PRODUCTS}
        categories={CATEGORY_LIST}
        width={width}
        mode="trade"
      />
    </MemoryRouter>,
  );
}

function blocks(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>("[data-section-type]"));
}

describe("above the stacking breakpoint", () => {
  it("places each section on the 12-column grid at its own column and row", () => {
    const { container } = renderPage(PAGE, STACK_WIDTH + 1);

    const canvas = screen.getByTestId("page-canvas");
    expect(canvas.dataset.stacked).toBe("false");

    const template = canvas.style.gridTemplateColumns;
    // A rail, twelve content columns each naming their own leading line, a rail.
    expect(template.startsWith("[bleed-start] minmax(")).toBe(true);
    expect(template.endsWith("[bleed-end]")).toBe(true);
    expect(template).toContain("repeat(12, [content-start] minmax(0, calc((var(--st-max-width)");
    expect(template).toContain(") [content-end] ");

    const [first, second] = blocks(container);
    if (!first || !second) throw new Error("both sections should render");

    // Sorted into reading order first: y ascending, then x.
    expect(first.style.gridColumn).toBe("content-start 1 / span 8");
    expect(first.style.gridRow).toBe("1 / span 4");
    expect(second.style.gridColumn).toBe("content-start 7 / span 6");
    expect(second.style.gridRow).toBe("5 / span 5");
  });

  it("caps the content columns, not an ancestor, so the max-width is escapable", () => {
    renderPage(PAGE, 1600);

    const template = screen.getByTestId("page-canvas").style.gridTemplateColumns;
    // Twelve columns, eleven gaps and two gutters come to exactly `--st-max-width`: the wrapper
    // this replaced was `max-width: var(--st-max-width)` with a gutter of 1.5 steps each side,
    // border-box, so 2 × 1.5 + 11 × 1 = 14 steps come off the top.
    expect(template).toContain("calc((var(--st-max-width) - calc(var(--st-space) * 14)) / 12)");
    // The rail is the gutter minus the column gap, because the gap sits between rail and column.
    expect(template).toContain("minmax(calc(var(--st-space) * 0.5), 1fr)");
  });
});

describe("below the stacking breakpoint", () => {
  it("makes every section full width and stacks them in y order", () => {
    const { container } = renderPage(PAGE, STACK_WIDTH - 1);

    const canvas = screen.getByTestId("page-canvas");
    expect(canvas.dataset.stacked).toBe("true");
    // The same rails, one content track. They survive stacking because bleed at phone width is
    // edge-to-edge with no side gutter — which is exactly what a promo banner is.
    expect(canvas.style.gridTemplateColumns).toBe(
      "[bleed-start] calc(var(--st-space) * 0.75) [content-start] minmax(0, 1fr) [content-end] calc(var(--st-space) * 0.75) [bleed-end]",
    );

    const rendered = blocks(container);
    for (const block of rendered) {
      expect(block.style.gridColumn).toBe("content-start / content-end");
      expect(block.style.gridRow).toBe("");
    }

    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(["First in reading order", "Second in reading order"]);
  });
});

describe("a page with no sections", () => {
  it("says so instead of rendering an empty canvas", () => {
    renderPage({ id: "blank", route: "/blank", name: "Blank", sections: [] }, 1200);

    expect(screen.getByText("This page has no sections yet")).not.toBeNull();
    expect(screen.queryByTestId("page-canvas")).toBeNull();
  });
});

function richText(id: string, settings: Record<string, boolean>): SectionInstance {
  return {
    id,
    type: "rich-text",
    settings: { ...defaultSettings("rich-text"), heading: id, ...settings },
    layout: { x: 2, y: 0, w: 6, h: 4 },
  };
}

function cellFor(container: HTMLElement, id: string): HTMLElement {
  const index = [...container.querySelectorAll("h2")].findIndex((h) => h.textContent === id);
  const cell = blocks(container)[index];
  if (!cell) throw new Error(`no cell rendered for ${id}`);
  return cell;
}

describe("a section that asks to bleed", () => {
  const bleeding: PageDoc = {
    id: "bleeding",
    route: "/",
    name: "Bleeding",
    sections: [richText("bleeds", { bleed: true })],
  };

  it("spans the rails instead of its columns, and keeps its rows", () => {
    const { container } = renderPage(bleeding, 1200);

    const cell = cellFor(container, "bleeds");
    expect(cell.style.gridColumn).toBe("bleed-start / bleed-end");
    // `x` and `w` stop mattering; `y` and `h` still place it down the page.
    expect(cell.style.gridRow).toBe("1 / span 4");
    expect(cell.dataset.bleed).toBe("true");
  });

  it("still reaches both rails when the page stacks", () => {
    const { container } = renderPage(bleeding, STACK_WIDTH - 1);

    expect(cellFor(container, "bleeds").style.gridColumn).toBe("bleed-start / bleed-end");
  });

  it("reads the one key the page layer owns, under the name the schema gives it", () => {
    const { container } = renderPage(
      { ...bleeding, sections: [richText("named", { [BLEED_SETTING]: true })] },
      1200,
    );

    expect(cellFor(container, "named").style.gridColumn).toBe("bleed-start / bleed-end");
  });

  it("stays in the content column when the setting is false, and when it is absent", () => {
    const { container } = renderPage(
      {
        ...bleeding,
        sections: [richText("explicitly-false", { bleed: false })],
      },
      1200,
    );

    const cell = cellFor(container, "explicitly-false");
    expect(cell.style.gridColumn).toBe("content-start 3 / span 6");
    expect(cell.dataset.bleed).toBeUndefined();

    const absent = renderPage({ ...bleeding, sections: [richText("absent", {})] }, 1200);
    const plain = cellFor(absent.container, "absent");
    expect(plain.style.gridColumn).toBe("content-start 3 / span 6");
    expect(plain.dataset.bleed).toBeUndefined();
  });
});

/**
 * The guard on the work that already shipped.
 *
 * The trade themes were composed against a hard content column. None of them declares the bleed
 * field, `defaultSettings` only supplies keys a def declares, and the rails reproduce the old
 * wrapper's gutter exactly — so every one of their sections must still place in the content track.
 * Filtering on `mode` keeps this honest once retail themes land: those are the ones that bleed.
 */
describe("the trade themes", () => {
  const trade = THEMES.filter((theme) => theme.mode === "trade");

  it("still covers the six themes that predate full-bleed", () => {
    expect(trade.length).toBeGreaterThanOrEqual(6);
  });

  it.each(trade.map((theme) => [theme.key, theme] as const))(
    "%s places every section in the content track, at both widths",
    (_key, theme) => {
      for (const width of [1200, 390]) {
        for (const page of theme.pages) {
          const { container, unmount } = renderPage(page, width);
          for (const cell of blocks(container)) {
            expect(cell.style.gridColumn).not.toContain("bleed-");
          }
          expect(container.querySelectorAll("[data-bleed]")).toHaveLength(0);
          unmount();
        }
      }
    },
  );
});
