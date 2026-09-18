import type { Category, Product } from "@/api/types";
import { GRID_COLUMNS, ROW_HEIGHT, STACK_WIDTH } from "../schema";
import type { PageDoc, SectionInstance } from "../schema";
import { SectionEmpty } from "./primitives";
import { SectionRenderer } from "./SectionRenderer";
import { step } from "./tokens";

export interface PageRendererProps {
  page: PageDoc;
  products: Product[];
  categories: Category[];
  /**
   * The canvas width in px. Drives stacking, so the device preview and a genuinely narrow browser
   * behave identically — and so this component is testable without a layout engine.
   */
  width: number;
  /** Scales the row unit for the gallery's miniature. 1 is full size. */
  scale?: number;
}

/** Ascending `y`, then ascending `x` — the reading order a stacked page follows. */
function inFlowOrder(sections: SectionInstance[]): SectionInstance[] {
  return [...sections].sort((a, b) => a.layout.y - b.layout.y || a.layout.x - b.layout.x);
}

/**
 * A page's sections on the 12-column canvas.
 *
 * Above `STACK_WIDTH` each section sits where its `layout` puts it. Below it every section spans
 * the full width and the page stacks in `y` order. Rows are `minmax(rowHeight, auto)`: the row
 * count in `layout.h` reserves height so nothing shifts as content settles, but a section whose
 * content runs long grows its row rather than clipping — a merchant's paragraph must never be cut
 * off because the block was dropped one row too short.
 */
export function PageRenderer({ page, products, categories, width, scale = 1 }: PageRendererProps) {
  const stacked = width < STACK_WIDTH;
  const ordered = inFlowOrder(page.sections);

  if (ordered.length === 0) {
    return (
      <SectionEmpty
        title="This page has no sections yet"
        hint="Open it in the editor and drop a block onto the canvas to start."
      />
    );
  }

  return (
    <div
      data-testid="page-canvas"
      data-stacked={stacked ? "true" : "false"}
      style={{
        display: "grid",
        gridTemplateColumns: stacked ? "minmax(0, 1fr)" : `repeat(${GRID_COLUMNS}, minmax(0, 1fr))`,
        gridAutoRows: stacked ? "auto" : `minmax(${ROW_HEIGHT * scale}px, auto)`,
        gap: step(1),
        alignItems: "stretch",
      }}
    >
      {ordered.map((section) => (
        <div
          key={section.id}
          data-section-type={section.type}
          style={
            stacked
              ? { gridColumn: "1 / -1", minWidth: 0 }
              : {
                  gridColumn: `${section.layout.x + 1} / span ${section.layout.w}`,
                  gridRow: `${section.layout.y + 1} / span ${section.layout.h}`,
                  minWidth: 0,
                }
          }
        >
          <SectionRenderer
            section={section}
            products={products}
            categories={categories}
            stacked={stacked}
          />
        </div>
      ))}
    </div>
  );
}
