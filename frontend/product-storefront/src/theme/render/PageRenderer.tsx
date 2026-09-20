import type { CSSProperties } from "react";

import type { Category, Product } from "@/api/types";
import { BLEED_SETTING, ROW_HEIGHT, STACK_WIDTH } from "../schema";
import type { PageDoc, SectionInstance, StoreMode } from "../schema";
import {
  contentColumnStyle,
  pageColumnGap,
  pageGridTemplateColumns,
  pageRowGap,
} from "./pageGrid";
import { SectionEmpty } from "./primitives";
import { SectionRenderer } from "./SectionRenderer";
import { bool } from "./settings";

export interface PageRendererProps {
  page: PageDoc;
  products: Product[];
  categories: Category[];
  /**
   * The canvas width in px. Drives stacking, so the device preview and a genuinely narrow browser
   * behave identically — and so this component is testable without a layout engine.
   */
  width: number;
  /** The store's mode, already narrowed by `resolveMode`. Handed to every section. */
  mode: StoreMode;
  /** Scales the row unit for the gallery's miniature. 1 is full size. */
  scale?: number;
}

/** Ascending `y`, then ascending `x` — the reading order a stacked page follows. */
function inFlowOrder(sections: SectionInstance[]): SectionInstance[] {
  return [...sections].sort((a, b) => a.layout.y - b.layout.y || a.layout.x - b.layout.x);
}

/** Whether this instance asked to span the whole canvas. Absent or false means it did not. */
function bleeds(section: SectionInstance): boolean {
  return bool(section.settings, BLEED_SETTING, false);
}

function placement(section: SectionInstance, stacked: boolean): CSSProperties {
  if (bleeds(section)) {
    return stacked
      ? { gridColumn: "bleed-start / bleed-end", minWidth: 0 }
      : {
          gridColumn: "bleed-start / bleed-end",
          gridRow: `${section.layout.y + 1} / span ${section.layout.h}`,
          minWidth: 0,
        };
  }
  if (stacked) return { gridColumn: "content-start / content-end", minWidth: 0 };
  return {
    // `content-start N` is the line before the Nth content column — the twelve columns each name
    // their leading line, so a section still places by `layout.x` and spans by `layout.w`.
    gridColumn: `content-start ${section.layout.x + 1} / span ${section.layout.w}`,
    gridRow: `${section.layout.y + 1} / span ${section.layout.h}`,
    minWidth: 0,
  };
}

/**
 * A page's sections on the 12-column canvas.
 *
 * Above `STACK_WIDTH` each section sits where its `layout` puts it. Below it every section spans
 * the full content width and the page stacks in `y` order. Rows are `minmax(rowHeight, auto)`: the
 * row count in `layout.h` reserves height so nothing shifts as content settles, but a section
 * whose content runs long grows its row rather than clipping — a merchant's paragraph must never
 * be cut off because the block was dropped one row too short.
 *
 * A section carrying `bleed: true` in its own settings spans the rails instead of its columns, at
 * every width. `BLEED_SETTING` is the one setting key this layer understands; everything else in
 * `settings` belongs to the section alone.
 */
export function PageRenderer({
  page,
  products,
  categories,
  width,
  mode,
  scale = 1,
}: PageRendererProps) {
  const stacked = width < STACK_WIDTH;
  const ordered = inFlowOrder(page.sections);

  if (ordered.length === 0) {
    return (
      <div style={contentColumnStyle(stacked)}>
        <SectionEmpty
          title="This page has no sections yet"
          hint="Open it in the editor and drop a block onto the canvas to start."
        />
      </div>
    );
  }

  return (
    <div
      data-testid="page-canvas"
      data-stacked={stacked ? "true" : "false"}
      style={{
        display: "grid",
        gridTemplateColumns: pageGridTemplateColumns(stacked),
        gridAutoRows: stacked ? "auto" : `minmax(${ROW_HEIGHT * scale}px, auto)`,
        // Column gap is the page's gutter between columns; the rails already carry the outer one.
        columnGap: pageColumnGap(stacked),
        rowGap: pageRowGap(),
        alignItems: "stretch",
      }}
    >
      {ordered.map((section) => (
        <div
          key={section.id}
          data-section-type={section.type}
          {...(bleeds(section) ? { "data-bleed": "true" } : {})}
          style={placement(section, stacked)}
        >
          <SectionRenderer
            section={section}
            products={products}
            categories={categories}
            stacked={stacked}
            width={width}
            mode={mode}
          />
        </div>
      ))}
    </div>
  );
}
