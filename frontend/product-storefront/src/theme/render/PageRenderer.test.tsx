/**
 * The 12-column canvas: placement above the breakpoint, full-width stacking in `y` order below it,
 * and a page with no sections at all.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { CATEGORY_LIST, PRODUCTS } from "@/api/fixtures";
import { STACK_WIDTH } from "../schema";
import type { PageDoc } from "../schema";
import { defaultSettings } from "../sections";
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
      <PageRenderer page={page} products={PRODUCTS} categories={CATEGORY_LIST} width={width} />
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
    expect(canvas.style.gridTemplateColumns).toBe("repeat(12, minmax(0, 1fr))");

    const [first, second] = blocks(container);
    if (!first || !second) throw new Error("both sections should render");

    // Sorted into reading order first: y ascending, then x.
    expect(first.style.gridColumn).toBe("1 / span 8");
    expect(first.style.gridRow).toBe("1 / span 4");
    expect(second.style.gridColumn).toBe("7 / span 6");
    expect(second.style.gridRow).toBe("5 / span 5");
  });
});

describe("below the stacking breakpoint", () => {
  it("makes every section full width and stacks them in y order", () => {
    const { container } = renderPage(PAGE, STACK_WIDTH - 1);

    const canvas = screen.getByTestId("page-canvas");
    expect(canvas.dataset.stacked).toBe("true");
    expect(canvas.style.gridTemplateColumns).toBe("minmax(0, 1fr)");

    const rendered = blocks(container);
    for (const block of rendered) {
      expect(block.style.gridColumn).toBe("1 / -1");
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
