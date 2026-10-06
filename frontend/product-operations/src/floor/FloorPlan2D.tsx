import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent as ReactPointerEvent } from "react";

import { LOCATION_KIND_COLOR } from "@/data/locationKindColor";
import { LOCATION_KIND_LABEL } from "@/data/locationKinds";
import { planBox } from "@/lib/floorGeometry";
import type { FloorRect, PlanBox } from "@/lib/floorGeometry";
import { Button } from "@/ui";
import { MinusCircleIcon, PlusIcon, RefreshIcon } from "@opsgrids/ui";

interface FloorPlan2DProps {
  /** Drawable rectangles only; the page lists the ones that could not be drawn. */
  rects: FloorRect[];
  /** What a search is pointing at. Highlighted rectangles get a heavy dashed outline, a pin and a
   *  label as well as the accent fill, so a match is never told apart by colour alone. */
  highlightIds: ReadonlySet<string>;
  selectedId: string | null;
  onSelect: (locationId: string) => void;
  /** Names the plan for a screen reader: "Floor plan of Ground floor". */
  label: string;
}

const ZOOM_STEP = 1.4;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 16;
/** Arrow keys pan by this share of the visible span. */
const KEY_PAN = 0.1;
/** A label is drawn inside a rectangle only when it fits; the drawer names every location anyway. */
const CHAR_WIDTH_EM = 0.62;
/** A press that moves this far (screen pixels) before it is released is a drag, not a click. */
const DRAG_THRESHOLD_PX = 5;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** The visible window onto the plan, as a viewBox: the home box scaled by `zoom` about its centre,
 *  then shifted by the pan offset (in plan millimetres). */
function viewFor(home: PlanBox, zoom: number, panX: number, panY: number): PlanBox {
  const width = home.width / zoom;
  const height = home.height / zoom;
  return {
    minX: home.minX + (home.width - width) / 2 + panX,
    minY: home.minY + (home.height - height) / 2 + panY,
    width,
    height,
  };
}

/**
 * A floor's published layout as a flat, read-only plan: one rectangle per placed location in
 * millimetre space, no WebGL needed. Pan by dragging anywhere on the plan, a rectangle included (a
 * press that moves more than a few pixels is a drag and selects nothing; one that does not is a
 * click), or with the arrow keys; zoom with the buttons, `+` / `-`, or Ctrl/⌘ plus the wheel. Plain
 * wheel scrolling is left to the page.
 *
 * Every rectangle is a real button — Tab reaches it, Enter or Space opens its bin — and carries its
 * code, kind and whether a search found it in its accessible name. A plan can have hundreds of
 * them, so a skip link ahead of the plan hands focus to what follows it. Strokes use
 * `vector-effect: non-scaling-stroke` so a zoomed-in plan keeps hairline outlines.
 */
export function FloorPlan2D({ rects, highlightIds, selectedId, onSelect, label }: FloorPlan2DProps) {
  const home = useMemo(() => planBox(rects), [rects]);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const svgRef = useRef<SVGSVGElement>(null);
  const endRef = useRef<HTMLParagraphElement>(null);
  const endId = useId();
  const drag = useRef<{ pointerId: number; x: number; y: number; startX: number; startY: number; panning: boolean } | null>(null);
  // Set once a press turns into a pan, so the click the browser may still send to the rectangle
  // under the pointer when it is released does not also open that bin.
  const dragged = useRef(false);

  const view = viewFor(home, zoom, pan.x, pan.y);
  const viewBox = `${view.minX} ${view.minY} ${view.width} ${view.height}`;

  function zoomBy(factor: number) {
    setZoom((current) => clamp(current * factor, MIN_ZOOM, MAX_ZOOM));
  }

  function reset() {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }

  function onKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    // Keys on a focused rectangle belong to that rectangle (Enter/Space); only the plan itself pans.
    if (event.target !== event.currentTarget) return;
    const step = view.width * KEY_PAN;
    if (event.key === "ArrowLeft") setPan((p) => ({ ...p, x: p.x - step }));
    else if (event.key === "ArrowRight") setPan((p) => ({ ...p, x: p.x + step }));
    else if (event.key === "ArrowUp") setPan((p) => ({ ...p, y: p.y - step }));
    else if (event.key === "ArrowDown") setPan((p) => ({ ...p, y: p.y + step }));
    else if (event.key === "+" || event.key === "=") zoomBy(ZOOM_STEP);
    else if (event.key === "-") zoomBy(1 / ZOOM_STEP);
    else if (event.key === "0") reset();
    else return;
    event.preventDefault();
  }

  // A native listener, not React's `onWheel`: React registers wheel listeners passive, and a passive
  // listener cannot `preventDefault`, so the browser would zoom the whole page along with the plan.
  // Ctrl/⌘ + wheel is also how a trackpad pinch arrives. Only that combination is taken; a plain
  // wheel still scrolls the page.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    function onWheel(event: WheelEvent) {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const factor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
      setZoom((current) => clamp(current * factor, MIN_ZOOM, MAX_ZOOM));
    }
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  function onPointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    dragged.current = false;
    drag.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      startX: event.clientX,
      startY: event.clientY,
      panning: false,
    };
  }

  function onPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    const current = drag.current;
    const svg = svgRef.current;
    if (!current || current.pointerId !== event.pointerId || !svg) return;
    if (!current.panning) {
      if (Math.hypot(event.clientX - current.startX, event.clientY - current.startY) < DRAG_THRESHOLD_PX) return;
      // Captured only once it is a drag: capturing at the press would send the release, and the click
      // after it, to the plan instead of to the rectangle a plain click was aimed at.
      current.panning = true;
      dragged.current = true;
      svg.setPointerCapture?.(event.pointerId);
    }
    const box = svg.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) return;
    const dx = ((event.clientX - current.x) / box.width) * view.width;
    const dy = ((event.clientY - current.y) / box.height) * view.height;
    current.x = event.clientX;
    current.y = event.clientY;
    setPan((p) => ({ x: p.x - dx, y: p.y - dy }));
  }

  function endDrag(event: ReactPointerEvent<SVGSVGElement>) {
    if (drag.current?.pointerId === event.pointerId) drag.current = null;
  }

  function selectByClick(locationId: string) {
    if (dragged.current) {
      dragged.current = false;
      return;
    }
    onSelect(locationId);
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted">
          Drag to pan, or focus the plan and use the arrow keys. Zoom with the buttons, + and −, or Ctrl/⌘ and the wheel.
        </p>
        <div className="flex items-center gap-1" role="group" aria-label="Plan zoom">
          <Button size="sm" variant="secondary" aria-label="Zoom in" onClick={() => zoomBy(ZOOM_STEP)} disabled={zoom >= MAX_ZOOM}>
            <PlusIcon className="size-4" />
          </Button>
          <Button size="sm" variant="secondary" aria-label="Zoom out" onClick={() => zoomBy(1 / ZOOM_STEP)} disabled={zoom <= MIN_ZOOM}>
            <MinusCircleIcon className="size-4" />
          </Button>
          <Button size="sm" variant="secondary" aria-label="Reset view" onClick={reset} leadingIcon={<RefreshIcon className="size-4" />}>
            Reset
          </Button>
        </div>
      </div>

      <a
        href={`#${endId}`}
        onClick={(event) => {
          // Same hand-off as the app's skip link: a fragment jump alone does not reliably move focus.
          event.preventDefault();
          endRef.current?.focus();
        }}
        className="sr-only focus:not-sr-only w-fit rounded-tile bg-surface px-3 py-2 text-sm font-semibold text-accent shadow-card"
      >
        Skip the floor plan
      </a>

      <svg
        ref={svgRef}
        viewBox={viewBox}
        role="group"
        aria-label={label}
        tabIndex={0}
        className="h-auto max-h-screen min-h-48 w-full touch-none rounded-tile bg-sunken"
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        {rects.map((rect) => (
          <PlanRect
            key={rect.locationId}
            rect={rect}
            highlighted={highlightIds.has(rect.locationId)}
            selected={rect.locationId === selectedId}
            onSelect={onSelect}
            onClick={selectByClick}
          />
        ))}
      </svg>
      <p ref={endRef} id={endId} tabIndex={-1} className="text-xs text-muted">
        End of the floor plan · {rects.length} location{rects.length === 1 ? "" : "s"}
      </p>
    </div>
  );
}

interface PlanRectProps {
  rect: FloorRect;
  highlighted: boolean;
  selected: boolean;
  /** Enter or Space on the rectangle. */
  onSelect: (locationId: string) => void;
  /** A click on it, which the plan withholds when the press was the end of a drag. */
  onClick: (locationId: string) => void;
}

function PlanRect({ rect, highlighted, selected, onSelect, onClick }: PlanRectProps) {
  const color = LOCATION_KIND_COLOR[rect.kind].cssVar;
  const short = Math.min(rect.width, rect.depth);
  const fontSize = clamp(short / 4, 80, 600);
  const fits = rect.code.length * fontSize * CHAR_WIDTH_EM <= rect.width;
  const cx = rect.x + rect.width / 2;
  const cy = rect.y + rect.depth / 2;
  // The pin sits on the rectangle's top edge, sized to the pointer rather than to the location.
  const pin = clamp(short / 2, 200, 700);

  return (
    <g
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`${rect.code}, ${LOCATION_KIND_LABEL[rect.kind]}${highlighted ? ", found by your search" : ""}`}
      className="cursor-pointer"
      onClick={() => onClick(rect.locationId)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect(rect.locationId);
        }
      }}
    >
      <title>{`${rect.code} · ${rect.name}`}</title>
      <rect
        x={rect.x}
        y={rect.y}
        width={rect.width}
        height={rect.depth}
        fill={highlighted ? "var(--color-accent)" : `var(${color})`}
        fillOpacity={highlighted ? 0.55 : 0.28}
        stroke={highlighted || selected ? "var(--color-accent)" : `var(${color})`}
        strokeWidth={highlighted ? 4 : selected ? 3 : 1.5}
        strokeDasharray={highlighted ? "10 4" : undefined}
        vectorEffect="non-scaling-stroke"
        data-highlighted={highlighted ? "true" : undefined}
      />
      {fits || highlighted ? (
        <text
          x={cx}
          y={cy}
          fontSize={fontSize}
          fontWeight={highlighted ? 700 : 500}
          textAnchor="middle"
          dominantBaseline="central"
          fill="var(--color-ink)"
          pointerEvents="none"
        >
          {rect.code}
        </text>
      ) : null}
      {highlighted ? (
        <path
          d={`M ${cx} ${rect.y} l ${-pin / 2} ${-pin} h ${pin} z`}
          fill="var(--color-accent)"
          stroke="var(--color-ink)"
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
          pointerEvents="none"
          data-pin="true"
        />
      ) : null}
    </g>
  );
}
