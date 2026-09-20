/**
 * Every section type has to come out of data alone, and a type this build does not know has to
 * degrade to a labelled placeholder instead of taking the page down with it.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { CATEGORY_LIST, PRODUCTS } from "@/api/fixtures";
import { defaultSettings, SECTION_DEFS } from "../sections";
import type { SectionInstance } from "../schema";
import { SECTION_COMPONENTS } from "./registry";
import { SectionRenderer } from "./SectionRenderer";

/** The two reference widths: the phone the retail work is designed at, and the desktop canvas. */
const PHONE_WIDTH = 390;
const DESKTOP_WIDTH = 1200;

/**
 * Every section's own root element. `footer-columns` is a real `<footer>` — the reference's footer
 * *is* the page's footer, so the landmark belongs on that element rather than on a `<section>`
 * wrapped around it to satisfy this sweep.
 */
const SECTION_ROOT = "section, footer";

function instance(type: string, overrides: Partial<SectionInstance> = {}): SectionInstance {
  return {
    id: `test-${type}`,
    type,
    settings: defaultSettings(type),
    layout: { x: 0, y: 0, w: 12, h: 8 },
    ...overrides,
  };
}

function renderSection(section: SectionInstance, stacked = false) {
  return render(
    <MemoryRouter>
      <SectionRenderer
        section={section}
        products={PRODUCTS}
        categories={CATEGORY_LIST}
        stacked={stacked}
        width={stacked ? PHONE_WIDTH : DESKTOP_WIDTH}
        mode="trade"
      />
    </MemoryRouter>,
  );
}

describe("the section library and the renderer agree", () => {
  it("has a component for every declared section type", () => {
    for (const def of SECTION_DEFS) {
      expect(SECTION_COMPONENTS[def.type], `no renderer for "${def.type}"`).toBeTypeOf("function");
    }
  });

  it("declares every type a component is registered for", () => {
    const declared = new Set(SECTION_DEFS.map((def) => def.type));
    for (const type of Object.keys(SECTION_COMPONENTS)) {
      expect(declared.has(type), `"${type}" has a renderer but no definition`).toBe(true);
    }
  });
});

describe.each(SECTION_DEFS.map((def) => [def.type, def.name] as const))(
  "%s renders from data alone",
  (type) => {
    it("renders its declared defaults without throwing", () => {
      const { container } = renderSection(instance(type));
      expect(container.querySelector(SECTION_ROOT)).not.toBeNull();
      expect(screen.queryByText("Section unavailable")).toBeNull();
    });

    it("renders with an empty settings bag — a document written before a field existed", () => {
      const { container } = renderSection(instance(type, { settings: {} }));
      expect(container.querySelector(SECTION_ROOT)).not.toBeNull();
      expect(screen.queryByText("Section unavailable")).toBeNull();
    });

    it("renders with no catalogue behind it", () => {
      const section = instance(type);
      render(
        <MemoryRouter>
          <SectionRenderer
            section={section}
            products={[]}
            categories={[]}
            stacked={false}
            width={DESKTOP_WIDTH}
            mode="trade"
          />
        </MemoryRouter>,
      );
      expect(screen.queryByText("Section unavailable")).toBeNull();
    });
  },
);

describe("an unknown section type", () => {
  it("degrades to a labelled placeholder instead of throwing", () => {
    renderSection(instance("carousel-of-the-future"));

    expect(screen.getByText("Section unavailable")).not.toBeNull();
    expect(screen.getByText(/Type: carousel-of-the-future/)).not.toBeNull();
    expect(screen.getByText(/came from a newer version/)).not.toBeNull();
  });
});

describe("a commerce section whose source matches nothing", () => {
  it("shows the section's own empty state rather than the whole catalogue", () => {
    renderSection(
      instance("product-grid", {
        settings: { ...defaultSettings("product-grid"), heading: "Empty grid", source: "category:nothing-here" },
      }),
    );

    expect(screen.getByText("Nothing to show here yet")).not.toBeNull();
    expect(screen.getByText("This category has no lines in the catalogue yet.")).not.toBeNull();
    expect(screen.queryByText(PRODUCTS[0]?.name ?? "")).toBeNull();
  });
});
