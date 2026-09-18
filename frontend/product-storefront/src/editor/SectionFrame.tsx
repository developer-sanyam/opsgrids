import type { CSSProperties, KeyboardEvent, PointerEvent } from "react";

import { cn } from "@opsgrids/ui";

import type { Category, Product } from "@/api/types";
import type { SectionDef, SectionInstance, StoreMode } from "@/theme/schema";
import { SectionRenderer } from "@/theme/render";

import { COLUMNS } from "./grid";

/**
 * One section on the canvas: the real storefront section, with an editor overlay on top of it.
 *
 * The section itself is rendered by `@/theme/render` — the same component a buyer loads — inside
 * an `inert` wrapper. That is what makes this canvas a preview rather than a sketch, and `inert`
 * is what stops the buttons and links inside a section from becoming tab stops in the editor or
 * from swallowing the drag.
 *
 * The overlay is one `<button>`: the selection target, the drag surface and the keyboard path.
 * The three resize handles are pointer affordances and nothing more — `aria-hidden`, not
 * focusable — because everything they do is already on the button under Shift and an arrow key.
 */

export type DragMode = "move" | "resize-x" | "resize-y" | "resize-xy";

interface SectionFrameProps {
  section: SectionInstance;
  def: SectionDef | undefined;
  products: Product[];
  categories: Category[];
  selected: boolean;
  canEdit: boolean;
  /** Below the breakpoint the grid is not editable, so the frame drops its drag affordances. */
  stacked: boolean;
  /** The width the preview is composed at — the same number the buyer's canvas would report. */
  width: number;
  /** The store's mode, so the preview shows the buyer's own card and vocabulary. */
  mode: StoreMode;
  /** Grid placement, or nothing at all when the page is stacked. */
  style: CSSProperties;
  /** The id of the shared `sr-only` paragraph describing the keyboard controls. */
  instructionsId: string;
  /** A callback ref, so the canvas can hold one map of frames and focus any of them. */
  frameRef: ((element: HTMLButtonElement | null) => void) | undefined;
  onSelect: () => void;
  onNudge: (dx: number, dy: number) => void;
  onScale: (dw: number, dh: number) => void;
  onDepth: (direction: "forward" | "backward") => void;
  onRemove: () => void;
  onDismiss: () => void;
  onDragStart: (mode: DragMode, event: PointerEvent<HTMLElement>) => void;
}

export function SectionFrame({
  section,
  def,
  products,
  categories,
  selected,
  canEdit,
  stacked,
  width,
  mode,
  style,
  instructionsId,
  frameRef,
  onSelect,
  onNudge,
  onScale,
  onDepth,
  onRemove,
  onDismiss,
  onDragStart,
}: SectionFrameProps) {
  const name = def?.name ?? section.type;
  const label = `${name}, column ${section.layout.x + 1}, row ${section.layout.y + 1}, ${
    section.layout.w
  } of ${COLUMNS} columns wide, ${section.layout.h} rows tall`;
  const draggable = canEdit && !stacked;

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Escape") {
      onDismiss();
      return;
    }
    if (!canEdit) return;

    if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      onRemove();
      return;
    }

    const arrow = ARROWS[event.key];
    if (!arrow) return;

    // Stacking order is the one layout change that still means something below the breakpoint,
    // where `x` and `w` are ignored; moving and resizing are not offered there.
    if (event.altKey) {
      if (arrow.dy === 0) return;
      event.preventDefault();
      onDepth(arrow.dy < 0 ? "backward" : "forward");
      return;
    }
    if (stacked) return;

    event.preventDefault();
    if (event.shiftKey) {
      onScale(arrow.dx, arrow.dy);
      return;
    }
    onNudge(arrow.dx, arrow.dy);
  }

  return (
    <li className="relative min-w-0" style={style}>
      {/* The buyer's own section. `inert` keeps its links and buttons out of the tab order and
          out of the editor's way; `pointer-events-none` lets every press reach the overlay. */}
      <div inert className="pointer-events-none h-full">
        <SectionRenderer
          section={section}
          products={products}
          categories={categories}
          stacked={stacked}
          width={width}
          mode={mode}
        />
      </div>

      <button
        ref={frameRef}
        type="button"
        aria-pressed={selected}
        aria-describedby={canEdit ? instructionsId : undefined}
        onClick={onSelect}
        onKeyDown={onKeyDown}
        onPointerDown={draggable ? (event) => onDragStart("move", event) : undefined}
        className={cn(
          "absolute inset-0 block text-left transition-shadow",
          draggable ? "cursor-grab active:cursor-grabbing" : "cursor-pointer",
          selected
            ? "ring-2 ring-accent ring-offset-2 ring-offset-ground"
            : "ring-1 ring-transparent hover:ring-accent/40",
        )}
        style={{ borderRadius: "var(--st-radius)" }}
      >
        <span className="sr-only">{label}</span>
      </button>

      {selected && draggable ? (
        <>
          <Handle
            className="right-0 top-1/2 h-8 w-2 -translate-y-1/2 translate-x-1/2 cursor-ew-resize"
            onPointerDown={(event) => onDragStart("resize-x", event)}
          />
          <Handle
            className="bottom-0 left-1/2 h-2 w-8 -translate-x-1/2 translate-y-1/2 cursor-ns-resize"
            onPointerDown={(event) => onDragStart("resize-y", event)}
          />
          <Handle
            className="bottom-0 right-0 size-3 translate-x-1/2 translate-y-1/2 cursor-nwse-resize"
            onPointerDown={(event) => onDragStart("resize-xy", event)}
          />
        </>
      ) : null}
    </li>
  );
}

/**
 * A pointer-only grab target. Not a button and not focusable on purpose: it duplicates
 * `Shift` + arrow on the section itself, and a focusable handle would triple the tab stops on the
 * canvas without adding a single capability.
 */
function Handle({
  className,
  onPointerDown,
}: {
  className: string;
  onPointerDown: (event: PointerEvent<HTMLElement>) => void;
}) {
  return (
    <span
      aria-hidden
      onPointerDown={onPointerDown}
      className={cn("absolute z-10 rounded-full bg-accent shadow-card", className)}
    />
  );
}

const ARROWS: Record<string, { dx: number; dy: number } | undefined> = {
  ArrowLeft: { dx: -1, dy: 0 },
  ArrowRight: { dx: 1, dy: 0 },
  ArrowUp: { dx: 0, dy: -1 },
  ArrowDown: { dx: 0, dy: 1 },
};
