import { useEffect, useId, useRef, useState } from "react";
import type { CSSProperties, DragEvent, PointerEvent, RefObject } from "react";

import { EmptyState, InlineAlert, LayoutIcon, TABLET_QUERY, useMediaQuery } from "@opsgrids/ui";

import type { Category, Product } from "@/api/types";
import type {
  Brand,
  PageDoc,
  SectionDef,
  SectionInstance,
  StoreMode,
  ThemeTokens,
} from "@/theme/schema";
import { ROW_HEIGHT, STACK_WIDTH } from "@/theme/schema";
import { ThemeFrame } from "@/theme/render";
import { useElementWidth } from "@/lib/useElementWidth";
import { DEVICE_WIDTHS } from "@/ui/deviceWidths";
import { useCoarsePointer } from "@/ui/useCoarsePointer";

import type { Device } from "./document";
import { dragTypeFrom, SECTION_DRAG_MIME } from "./dragTypes";
import { bottomRow, cellFromPoint, COLUMNS, gridSteps, stackOrder } from "./grid";
import type { DragMode } from "./SectionFrame";
import { SectionFrame } from "./SectionFrame";
import { findDef } from "./sectionLibrary";

/**
 * The middle pane: the merchant's page, laid out by exactly the rule the storefront uses.
 *
 * The grid here is the same CSS Grid `PageRenderer` builds — twelve `1fr` columns, rows of
 * `ROW_HEIGHT` that may grow, a gap of one spacing step — because a canvas that positions sections
 * its own way is a canvas that lies.
 *
 * **The page is composed at a design width and scaled to fit, never laid out at whatever width
 * this pane happens to have.** The two are not the same thing, and conflating them was a real
 * defect: inside the app shell's 1600px cap, minus the product rail and two editor panes, this
 * pane is about 600px even on a 2560px display — under the schema's `STACK_WIDTH`. Deriving
 * "stacked" from the pane width therefore disabled layout editing for every user at every window
 * size. `stacked` now follows the *previewed* width, which is what the merchant chose with the
 * device toggle and what a buyer will actually get, and the composition is scaled down to fit the
 * room available — the same trick the gallery's miniature uses. `STACK_WIDTH` is a buyer-facing
 * contract in the schema and is not something the editor may lower to suit its own furniture.
 */

/**
 * **The mirror grid deliberately has no bleed rails.**
 *
 * `PageRenderer` places a section carrying `BLEED_SETTING` across two extra rails either side of
 * the twelve columns. This grid does not reproduce them, so a bleeding section previews inside the
 * content track here and reaches the canvas edge on the buyer-facing route. That is a known and
 * bounded v1 gap, taken on purpose: every pointer position on this canvas resolves against
 * `gridRef`'s width divided by twelve, and the drop outline and the grid lines are a separate
 * twelve-column overlay on `inset-0`. Adding rails moves all three off the columns they are meant
 * to describe, and a drop that lands a column from the cursor is a worse defect than a preview
 * that is one gutter narrow.
 */

/** Empty rows kept under the last section, so there is always somewhere to drop. */
const TRAILING_ROWS = 4;
const MIN_ROWS = 12;

/**
 * The width the desktop preview is composed at — the same 1280 the gallery miniature uses, so a
 * theme looks identical in the gallery, in the editor and on the buyer-facing route.
 */
const DESKTOP_DESIGN_WIDTH = 1280;

/** What the readout above the canvas calls each preview. */
const DEVICE_LABELS: Record<Device, string> = {
  desktop: "Desktop",
  tablet: "Tablet",
  mobile: "Phone",
};

interface CanvasProps {
  page: PageDoc;
  library: SectionDef[];
  tokens: ThemeTokens;
  brand: Brand;
  /** The store's mode, narrowed by the shell. Sections preview as the buyer will see them. */
  mode: StoreMode;
  products: Product[];
  categories: Category[];
  device: Device;
  selectedId: string | null;
  canEdit: boolean;
  /** Replaced by the shell when focus should move to a section — after adding or duplicating one. */
  focusRequest: { id: string; token: number } | null;
  onSelect: (id: string | null) => void;
  /** A pointer drag: fires on every cell crossed, so it is deliberately silent. */
  onMove: (id: string, x: number, y: number) => void;
  onResize: (id: string, w: number, h: number) => void;
  /** The keyboard equivalents. One key, one move — the shell announces each of these. */
  onNudge: (id: string, x: number, y: number) => void;
  onScale: (id: string, w: number, h: number) => void;
  onDepth: (id: string, direction: "forward" | "backward") => void;
  onRemove: (id: string) => void;
  onDragEnd: () => void;
  onDropSection: (def: SectionDef, at: { x: number; y: number }) => void;
}

export function Canvas({
  page,
  library,
  tokens,
  brand,
  mode,
  products,
  categories,
  device,
  selectedId,
  canEdit,
  focusRequest,
  onSelect,
  onMove,
  onResize,
  onNudge,
  onScale,
  onDepth,
  onRemove,
  onDragEnd,
  onDropSection,
}: CanvasProps) {
  const instructionsId = useId();
  const scrollRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const composedRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const frames = useRef(new Map<string, HTMLButtonElement | null>());

  const [drag, setDrag] = useState<Drag | null>(null);
  const [dropAt, setDropAt] = useState<{
    x: number;
    y: number;
    w: number;
    h: number;
  } | null>(null);

  const gap = tokens.space;
  // The width the page is composed at: the merchant's chosen preview, not this pane's width.
  const designWidth = DEVICE_WIDTHS[device] ?? DESKTOP_DESIGN_WIDTH;
  const touch = useCoarsePointer();
  // The width presets are only on screen from the tablet breakpoint up; below it there is nothing to "choose above".
  const hasPresets = useMediaQuery(TABLET_QUERY, true);
  const stacked = designWidth < STACK_WIDTH;
  const editable = canEdit && !stacked;

  const rows = Math.max(MIN_ROWS, bottomRow(page.sections) + TRAILING_ROWS);

  // How much of that width this pane can show. Scaled down to fit, never up — a phone preview
  // blown up to fill a desktop pane would misrepresent the type size a buyer gets.
  const available = useElementWidth(viewportRef, designWidth);
  const scale = available > 0 ? Math.min(1, available / designWidth) : 1;
  const scaled = scale < 1;
  // `transform` does not shrink a layout box, so the clip box needs the composed height to
  // multiply. Reserved from the grid until it is measured, so nothing jumps on first paint.
  const composedHeight = useElementHeight(composedRef, rows * (ROW_HEIGHT + gap));

  useEffect(() => {
    if (!focusRequest) return;
    frames.current.get(focusRequest.id)?.focus();
  }, [focusRequest]);

  const ordered = stacked ? stackOrder(page.sections) : page.sections;

  /**
   * A point on screen, as a grid cell.
   *
   * Every pointer position is divided by `scale` first: the rect of a transformed element is its
   * *painted* box, while the grid it describes is laid out at the design width. Mixing the two is
   * how a drop lands two columns from the cursor.
   */
  function cellAt(clientX: number, clientY: number): { x: number; y: number } {
    const rect = gridRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return cellFromPoint(
      (clientX - rect.left) / scale,
      (clientY - rect.top) / scale,
      rect.width / scale,
      gap,
    );
  }

  function startDrag(section: SectionInstance, mode: DragMode, event: PointerEvent<HTMLElement>) {
    if (!editable || event.button !== 0) return;
    // Suppresses the text selection a drag across a page of copy would otherwise make — and, as a
    // side effect, the focus `mousedown` would have moved. Chrome confirmed that: after a click,
    // `document.activeElement` was `<body>`, so arrow keys did nothing until the user tabbed all
    // the way back to the section. Focus is therefore moved deliberately, from either the section
    // or a resize handle, so picking something up with a mouse leaves the keyboard ready to nudge.
    event.preventDefault();
    onSelect(section.id);
    frames.current.get(section.id)?.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({
      id: section.id,
      mode,
      pointerX: event.clientX,
      pointerY: event.clientY,
      layout: section.layout,
    });
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!drag) return;
    const rect = gridRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    const steps = gridSteps(rect.width / scale, gap);
    const dx = Math.round((event.clientX - drag.pointerX) / scale / steps.column);
    const dy = Math.round((event.clientY - drag.pointerY) / scale / steps.row);

    if (drag.mode === "move") {
      onMove(drag.id, drag.layout.x + dx, drag.layout.y + dy);
      return;
    }
    const w = drag.mode === "resize-y" ? drag.layout.w : drag.layout.w + dx;
    const h = drag.mode === "resize-x" ? drag.layout.h : drag.layout.h + dy;
    onResize(drag.id, w, h);
  }

  function endDrag() {
    if (!drag) return;
    setDrag(null);
    onDragEnd();
  }

  function onDragOver(event: DragEvent<HTMLDivElement>) {
    if (!editable || !event.dataTransfer.types.includes(SECTION_DRAG_MIME)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    // `getData` is deliberately empty during a drag in every browser — only `types` is readable
    // before the drop — so the section type rides along as a second, empty MIME entry.
    const type = dragTypeFrom(event.dataTransfer.types);
    const def = type === null ? undefined : findDef(library, type);
    const at = cellAt(event.clientX, event.clientY);
    setDropAt({
      ...at,
      w: def?.defaultLayout.w ?? COLUMNS,
      h: def?.defaultLayout.h ?? 4,
    });
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    setDropAt(null);
    if (!editable) return;
    const def = findDef(library, event.dataTransfer.getData(SECTION_DRAG_MIME));
    if (!def) return;
    event.preventDefault();
    onDropSection(def, cellAt(event.clientX, event.clientY));
  }

  return (
    <div
      ref={scrollRef}
      tabIndex={-1}
      className="h-full overflow-auto bg-ground p-4 focus:outline-none sm:p-6"
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      <p id={instructionsId} className="sr-only">
        Arrow keys move the section one column or row. Shift and an arrow key resize it. Alt with up
        or down changes its place in the page order. Delete removes it. Escape clears the selection.
      </p>

      <div ref={viewportRef}>
        <p className="mb-2 text-center text-xs text-muted tnum">
          {DEVICE_LABELS[device]} · {designWidth}px
          {scaled ? ` · shown at ${Math.round(scale * 100)}%` : ""}
        </p>

        {/* The clip box. It is the composition's *painted* size, because `transform` leaves the
            layout box at the design width — without the clip, a 1280px-wide page scaled to a
            600px pane would still take 1280px of layout and swallow the pane beside it. */}
        <div
          className="relative mx-auto"
          style={{
            width: `${designWidth * scale}px`,
            maxWidth: "100%",
            ...(scaled ? { height: `${composedHeight * scale}px`, overflow: "hidden" } : {}),
          }}
        >
          <div
            ref={composedRef}
            style={{
              width: `${designWidth}px`,
              ...(scaled ? { transform: `scale(${scale})`, transformOrigin: "top left" } : {}),
            }}
          >
            <ThemeFrame
              tokens={tokens}
              style={{ borderRadius: "var(--radius-card)", overflow: "hidden" }}
            >
              <StoreBar brand={brand} route={page.route} />

              {stacked && canEdit ? (
                <div className="p-4">
                  <InlineAlert tone="info" title="You're editing the phone width" live="off">
                    Sections run full width here and follow the order they sit in down the page, so
                    there is nothing to move sideways.{" "}
                    {touch
                      ? "Move up and Move down on the Sections tab still change that order."
                      : hasPresets
                        ? "Alt with up or down still changes that order. Choose Desktop or Tablet above to lay the page out."
                        : "Alt with up or down still changes that order."}
                  </InlineAlert>
                </div>
              ) : null}

              {/* The page's own gutter lives on this wrapper, never on the grid: the grid element
                  is what every pointer position resolves against, and a padded box would report a
                  width two gutters wider than the columns it draws. */}
              <div
                className="relative"
                style={{ padding: `${gap}px` }}
                onDragOver={onDragOver}
                onDragLeave={() => setDropAt(null)}
                onDrop={onDrop}
                onPointerDown={(event) => {
                  // A press on the canvas itself, not on a section, clears the selection.
                  if (event.target === event.currentTarget) onSelect(null);
                }}
              >
                {page.sections.length === 0 ? (
                  <div ref={gridRef} style={{ minHeight: `${MIN_ROWS * ROW_HEIGHT}px` }}>
                    <EmptyState
                      icon={<LayoutIcon className="size-6" />}
                      title="This page is empty"
                      description={
                        canEdit
                          ? "Drag a section from the left onto the page, or press Enter on one to add it at the bottom."
                          : "Nothing has been added to this page yet."
                      }
                    />
                  </div>
                ) : (
                  <div
                    ref={gridRef}
                    className="relative"
                    style={{
                      display: "grid",
                      gridTemplateColumns: stacked
                        ? "minmax(0, 1fr)"
                        : `repeat(${COLUMNS}, minmax(0, 1fr))`,
                      gridAutoRows: stacked ? "auto" : `minmax(${ROW_HEIGHT}px, auto)`,
                      gap: `${gap}px`,
                      // Reserved before anything renders, so adding a section never shifts the page.
                      minHeight: stacked ? undefined : `${rows * (ROW_HEIGHT + gap)}px`,
                    }}
                  >
                    {drag || dropAt ? <GridLines gap={gap} /> : null}
                    {dropAt && !stacked ? <DropOutline at={dropAt} /> : null}

                    <ul className="contents">
                      {ordered.map((section) => (
                        <SectionFrame
                          key={section.id}
                          section={section}
                          def={findDef(library, section.type)}
                          products={products}
                          categories={categories}
                          selected={section.id === selectedId}
                          canEdit={canEdit}
                          stacked={stacked}
                          width={designWidth}
                          mode={mode}
                          style={frameStyle(section, stacked)}
                          instructionsId={instructionsId}
                          frameRef={(element) => {
                            frames.current.set(section.id, element);
                          }}
                          onSelect={() => onSelect(section.id)}
                          onNudge={(dx, dy) =>
                            onNudge(section.id, section.layout.x + dx, section.layout.y + dy)
                          }
                          onScale={(dw, dh) =>
                            onScale(section.id, section.layout.w + dw, section.layout.h + dh)
                          }
                          onDepth={(direction) => onDepth(section.id, direction)}
                          onRemove={() => {
                            onRemove(section.id);
                            // The frame is about to unmount; focus lands on the canvas rather than on
                            // the document body, which is where a keyboard user would otherwise be left.
                            scrollRef.current?.focus();
                          }}
                          onDismiss={() => onSelect(null)}
                          onDragStart={(mode, event) => startDrag(section, mode, event)}
                        />
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </ThemeFrame>
          </div>
        </div>
      </div>
    </div>
  );
}

interface Drag {
  id: string;
  mode: DragMode;
  pointerX: number;
  pointerY: number;
  layout: SectionInstance["layout"];
}

function frameStyle(section: SectionInstance, stacked: boolean): CSSProperties {
  if (stacked) return { gridColumn: "1 / -1" };
  return {
    gridColumn: `${section.layout.x + 1} / span ${section.layout.w}`,
    gridRow: `${section.layout.y + 1} / span ${section.layout.h}`,
  };
}

/** The twelve columns, shown only while something is being dragged. */
function GridLines({ gap }: { gap: number }) {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 grid"
      style={{
        gridTemplateColumns: `repeat(${COLUMNS}, minmax(0, 1fr))`,
        gap: `${gap}px`,
        padding: `${gap}px`,
      }}
    >
      {Array.from({ length: COLUMNS }, (_, index) => (
        <div key={index} className="h-full rounded-tile bg-accent/5 ring-1 ring-accent/20" />
      ))}
    </div>
  );
}

function DropOutline({ at }: { at: { x: number; y: number; w: number; h: number } }) {
  return (
    <div
      aria-hidden
      className="pointer-events-none rounded-tile border-2 border-dashed border-accent bg-accent/10"
      style={{
        gridColumn: `${at.x + 1} / span ${Math.min(at.w, COLUMNS - at.x)}`,
        gridRow: `${at.y + 1} / span ${at.h}`,
      }}
    />
  );
}

/** The storefront's own header, so the preview reads as a shop rather than a pile of boxes. */
function StoreBar({ brand, route }: { brand: Brand; route: string }) {
  return (
    <div
      aria-hidden
      className="flex items-center justify-between gap-4 px-4 py-3"
      style={{ borderBottom: "1px solid var(--st-border)" }}
    >
      <span className="text-sm font-bold" style={{ fontFamily: "var(--st-font-heading)" }}>
        {brand.logoText || brand.name}
      </span>
      <span className="text-xs" style={{ color: "var(--st-muted)" }}>
        {route}
      </span>
    </div>
  );
}

/**
 * The *unscaled* height of the composition, which only the clip box needs.
 *
 * `contentRect` is the pre-transform layout box, which is exactly right here: the clip box wants
 * the composed height so it can multiply by the scale itself. `@/lib/useElementWidth` is the
 * shared measurement for widths; this is its one-off counterpart rather than a second copy of it.
 */
function useElementHeight(ref: RefObject<HTMLElement | null>, fallback: number): number {
  const [height, setHeight] = useState(fallback);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (typeof ResizeObserver === "undefined") {
      setHeight(element.getBoundingClientRect().height || fallback);
      return;
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setHeight(entry.contentRect.height);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, fallback]);

  return height;
}
