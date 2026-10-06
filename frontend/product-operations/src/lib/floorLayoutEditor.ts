/**
 * Pure state helpers for the floor layout editor (Fulfilment plan §2/F7) — converting a loaded
 * `LayoutGeometryOut` into the editor's own working shape, converting edits back into the
 * `LayoutGeometryIn` the server's full-replace `PUT .../geometry` expects, and the small bits of
 * arithmetic (default placement size and position, auto-arranging a floor's unplaced locations,
 * node code generation) the canvas itself needs. No React here, so the state transitions are
 * unit-testable without a DOM.
 *
 * Overlap and disconnected-graph *validation* is deliberately **not** reimplemented here — the
 * server's `POST /layout-versions/{id}/validate` (`app.domain.overlapping_placements`/
 * `disconnected_required_nodes`) is the one place that logic lives, so the editor's "can I
 * publish" answer can never drift from the one the publish endpoint itself enforces.
 */
import type {
  LayoutGeometryIn,
  LayoutGeometryOut,
  LocationKind,
  LocationOut,
  WalkEdgeIn,
  WalkNodeIn,
} from "@/api/types";
import { flattenTree } from "./locationTree";

export interface EditorPlacement {
  locationId: string;
  x: number;
  y: number;
  z: number | null;
  width: number;
  depth: number;
  height: number | null;
}

export interface EditorNode {
  code: string;
  locationId: string | null;
  x: number;
  y: number;
  z: number | null;
}

export interface EditorEdge {
  fromCode: string;
  toCode: string;
  distanceMm: number;
  isTraversable: boolean;
  bidirectional: boolean;
}

export interface EditorState {
  placements: EditorPlacement[];
  nodes: EditorNode[];
  edges: EditorEdge[];
}

export const EMPTY_STATE: EditorState = { placements: [], nodes: [], edges: [] };

/** The server's bound on every placement coordinate (`LocationPlacementIn.x_mm`/`y_mm`, `le=1_000_000`). */
export const MAX_COORDINATE_MM = 1_000_000;

/** The canvas's zoom steps, as multiples of "fit" — the first is the whole plan in view. */
export const CANVAS_ZOOM_STEPS: readonly number[] = [1, 1.5, 2, 3, 4];

export function geometryToEditorState(geometry: LayoutGeometryOut): EditorState {
  return {
    placements: geometry.placements.map((p) => ({
      locationId: p.location_id,
      x: p.x_mm,
      y: p.y_mm,
      z: p.z_mm,
      width: p.width_mm,
      depth: p.depth_mm,
      height: p.height_mm,
    })),
    nodes: geometry.walk_nodes.map((n) => ({
      code: n.code,
      locationId: n.location_id,
      x: n.x_mm,
      y: n.y_mm,
      z: n.z_mm,
    })),
    edges: geometry.walk_edges.map((e) => ({
      fromCode: e.from_code,
      toCode: e.to_code,
      distanceMm: e.distance_mm,
      isTraversable: e.is_traversable,
      bidirectional: e.bidirectional,
    })),
  };
}

export function editorStateToGeometryIn(state: EditorState): LayoutGeometryIn {
  const walk_nodes: WalkNodeIn[] = state.nodes.map((n) => ({
    code: n.code,
    location_id: n.locationId,
    x_mm: Math.round(n.x),
    y_mm: Math.round(n.y),
    z_mm: n.z,
  }));
  const walk_edges: WalkEdgeIn[] = state.edges.map((e) => ({
    from_code: e.fromCode,
    to_code: e.toCode,
    distance_mm: Math.round(e.distanceMm),
    is_traversable: e.isTraversable,
    bidirectional: e.bidirectional,
  }));
  return {
    placements: state.placements.map((p) => ({
      location_id: p.locationId,
      x_mm: Math.round(p.x),
      y_mm: Math.round(p.y),
      z_mm: p.z,
      width_mm: Math.round(p.width),
      depth_mm: Math.round(p.depth),
      height_mm: p.height,
    })),
    walk_nodes,
    walk_edges,
  };
}

/** Deep-equal by value (via the same JSON shape both states round-trip through) — good enough
 *  for "has this draft changed since it last loaded or saved" without a diffing library, since
 *  editor state is always small (a floor's worth of locations and walk-graph nodes). */
export function statesEqual(a: EditorState, b: EditorState): boolean {
  const normalize = (s: EditorState) => ({
    placements: [...s.placements].sort((x, y) => x.locationId.localeCompare(y.locationId)),
    nodes: [...s.nodes].sort((x, y) => x.code.localeCompare(y.code)),
    edges: [...s.edges]
      .map((e) => ({ ...e }))
      .sort((x, y) => `${x.fromCode}\u0000${x.toCode}`.localeCompare(`${y.fromCode}\u0000${y.toCode}`)),
  });
  return JSON.stringify(normalize(a)) === JSON.stringify(normalize(b));
}

/** The walk node "for" a location — stable across saves (derived from the location's own id, not
 *  a counter), so re-saving the same draft twice never renames a node a previous save created. */
export function locationNodeCode(locationId: string): string {
  return `L-${locationId.replace(/[^A-Za-z0-9]/g, "").slice(0, 8).toUpperCase()}`;
}

/** The next free junction code (`J-1`, `J-2`, …) given the codes already in use. */
export function nextJunctionCode(existingCodes: ReadonlySet<string>): string {
  let n = 1;
  while (existingCodes.has(`J-${n}`)) n += 1;
  return `J-${n}`;
}

/** Every location in the warehouse not yet placed on this draft — the "add to canvas" sidebar. */
export function unplacedLocations(all: LocationOut[], state: EditorState): LocationOut[] {
  const placed = new Set(state.placements.map((p) => p.locationId));
  return all.filter((l) => !placed.has(l.id));
}

/** A floor as people name it: "Floor 0", or "Floor 0 — Ground" when it has a name. */
export function floorTitle(floor: { floor_number: number; name: string }): string {
  return floor.name ? `Floor ${floor.floor_number} — ${floor.name}` : `Floor ${floor.floor_number}`;
}

/** A location the draft does not have that another floor's published layout already does. */
export interface ElsewhereLocation {
  location: LocationOut;
  /** The floor it is on, as `floorTitle` names it. */
  floorTitle: string;
}

/**
 * Splits locations that are not on this draft into those that are on no floor — what Auto-arrange
 * and the unplaced list offer — and those another floor's published layout already holds.
 *
 * Publishing a layout writes its floor onto every location it places, and "find stock" and put-away
 * guidance read a location on two published floors as being on whichever was published last. So a
 * location that is already on a floor is only ever placed here as a deliberate act, one at a time,
 * never as part of a bulk arrange. `onOtherFloors` maps a location id to the title of that floor.
 */
export function splitByFloor(
  notOnDraft: readonly LocationOut[],
  onOtherFloors: ReadonlyMap<string, string>,
): { unplaced: LocationOut[]; elsewhere: ElsewhereLocation[] } {
  const unplaced: LocationOut[] = [];
  const elsewhere: ElsewhereLocation[] = [];
  for (const location of notOnDraft) {
    const floor = onOtherFloors.get(location.id);
    if (floor === undefined) unplaced.push(location);
    else elsewhere.push({ location, floorTitle: floor });
  }
  return { unplaced, elsewhere };
}

/* ---------------------------------------------------------------------------------------------
 * Screen pixels on the canvas.
 *
 * The canvas is drawn in millimetres and scaled to fit, so anything sized in millimetres is a few
 * pixels on a phone. Handles and walk nodes are sized from the pixels they must be instead.
 * ------------------------------------------------------------------------------------------- */

/** The side of the least target a finger can be expected to hit (the design system's touch size). */
export const TOUCH_TARGET_PX = 44;

/** How far a finger or pen may wander between touching down and lifting and still be a tap. */
export const TAP_SLOP_PX = 10;

/**
 * Millimetres one screen pixel spans, from the canvas's viewBox and its rendered size — `null`
 * before it has been measured. The viewBox is fitted inside the box ("meet"), so the scale is the
 * tighter of the two axes: a canvas capped in height is drawn smaller than its width alone says.
 */
export function mmPerPixel(
  viewBox: { width: number; height: number },
  rendered: { width: number; height: number } | null,
): number | null {
  if (rendered === null || rendered.width <= 0 || rendered.height <= 0) return null;
  const pixelsPerMm = Math.min(rendered.width / viewBox.width, rendered.height / viewBox.height);
  return pixelsPerMm > 0 ? 1 / pixelsPerMm : null;
}

/** A length given in screen pixels, in canvas millimetres — never less than `floorMm`, which is
 *  also what it is wherever the canvas has not been measured. */
export function pixelsToMm(pixels: number, mmPerPx: number | null, floorMm: number): number {
  return mmPerPx === null ? floorMm : Math.max(floorMm, Math.round(pixels * mmPerPx));
}

/** Straight-line distance between two points, rounded to a whole millimetre — the default a new
 *  edge's distance field starts from (the user can always type a longer real walking distance;
 *  this just saves typing the common case). */
export function euclideanDistanceMm(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.round(Math.hypot(a.x - b.x, a.y - b.y));
}

/* ---------------------------------------------------------------------------------------------
 * Footprints, labels and auto-arrange.
 *
 * A placement's x/y/width/depth are the one thing the server's publish gate checks for overlap
 * (`app.domain.overlapping_placements`: strict axis-aligned boxes, edges that merely touch are
 * fine). It has no notion of containment, so a rack drawn around its bins is a *failed* publish:
 * every location, container or not, gets its own footprint, and a hierarchy is shown by grouping
 * and order, never by nesting. Everything below keeps footprints disjoint by construction. The
 * overlap *verdict* still comes from the server; these helpers only avoid creating one.
 * ------------------------------------------------------------------------------------------- */

export interface Footprint {
  width: number;
  depth: number;
}

/** What a location of each kind is drawn at, in millimetres — schematic, not surveyed: roughly
 *  what the thing is in a warehouse, so a rack reads bigger than the bins on it. A zone is a band
 *  whose width is stretched to the section it heads (see `arrangeUnplaced`); this is its floor. */
const KIND_FOOTPRINT_MM: Record<LocationKind, Footprint> = {
  zone: { width: 4000, depth: 800 },
  aisle: { width: 3000, depth: 800 },
  rack: { width: 2400, depth: 1000 },
  shelf: { width: 1800, depth: 600 },
  bin: { width: 1000, depth: 600 },
  staging: { width: 3000, depth: 2000 },
  pack_station: { width: 1800, depth: 1200 },
  dock: { width: 3000, depth: 2000 },
  receiving: { width: 3000, depth: 2000 },
  returns: { width: 3000, depth: 2000 },
  quarantine: { width: 2000, depth: 1500 },
};

/** The label size an auto-arranged tile is built to carry, in viewBox units (millimetres). */
export const ARRANGED_LABEL_FONT_MM = 240;
const LABEL_MIN_FONT_MM = 60;
const LABEL_MAX_FONT_MM = 400;
/** Average glyph advance as a share of the font size — close enough for sizing a tile around a
 *  code, which is mostly capitals, digits and hyphens. */
const GLYPH_WIDTH_EM = 0.6;
/** A label's cap on how much of its tile's depth it may take. */
const LABEL_DEPTH_SHARE = 0.4;

/** The tile width that fits `label` at `ARRANGED_LABEL_FONT_MM` with one em of padding, rounded
 *  up to a hundred millimetres so every footprint stays on a tidy grid. */
export function labelWidthMm(label: string): number {
  return Math.ceil(((label.length * GLYPH_WIDTH_EM + 1) * ARRANGED_LABEL_FONT_MM) / 100) * 100;
}

/**
 * The font size (viewBox units) a placement's label is drawn at: as large as fits inside the
 * rectangle — by width for the label's length and by depth — within a sane range. Sizing the
 * text from the rectangle, not a fixed number, is what keeps a long code inside its tile and a
 * short one from shouting; the canvas zoom does the rest on a small screen.
 */
export function labelFontSizeMm(width: number, depth: number, label: string): number {
  const byWidth = width / (label.length * GLYPH_WIDTH_EM + 1);
  const byDepth = depth * LABEL_DEPTH_SHARE;
  return Math.round(Math.min(LABEL_MAX_FONT_MM, Math.max(LABEL_MIN_FONT_MM, Math.min(byWidth, byDepth))));
}

/** A location's footprint when it is first put on the floor: its kind's size, widened where its
 *  code would not otherwise fit. */
export function footprintFor(kind: LocationKind, code: string): Footprint {
  const base = KIND_FOOTPRINT_MM[kind];
  return { width: Math.max(base.width, labelWidthMm(code)), depth: base.depth };
}

/** Clear space kept around everything the helpers place. */
const MARGIN_MM = 500;
/** Between neighbours of one parent, and between the wrapped lines of one row. */
const GAP_MM = 300;
/** Between the children of two different parents in the same row — the aisle that shows where
 *  one group ends and the next begins. */
const AISLE_GAP_MM = 900;
/** Between a parent's row and the row of its children beneath it. */
const LEVEL_GAP_MM = 600;
/** Between one zone's section and the next. */
const SECTION_GAP_MM = 1200;
/** A row wraps beyond this width, so a phone's worth of canvas still shows a legible grid. It
 *  grows with the number of locations so a big floor stays roughly square, not a long strip. */
const ROW_MAX_WIDTH_MM = 9000;

interface ArrangeItem {
  location: LocationOut;
  /** The parent this item is grouped under — `null` for the root of an arranged subtree. */
  parentId: string | null;
  width: number;
  depth: number;
}

/** One root and everything beneath it, one row-group per tree depth (`levels[0]` is the root, or
 *  a run of root-level locations with nothing beneath them). `band` stretches the root across the
 *  section like a zone heading. */
interface ArrangeSection {
  levels: ArrangeItem[][];
  band: boolean;
}

interface PositionedTile {
  item: ArrangeItem;
  x: number;
  y: number;
  width: number;
}

function toItem(location: LocationOut): ArrangeItem {
  const { width, depth } = footprintFor(location.kind, location.code);
  return { location, parentId: location.parent_id, width, depth };
}

/**
 * Splits `unplaced` into sections, in `flattenTree`'s order (siblings by `pick_sequence`, nulls
 * last, then `code`). A location whose parent is not itself unplaced — the parent is already on
 * the floor, or is not in the list at all — is the root of its own subtree. A root with nothing
 * beneath it that is not a zone is *loose*: runs of those share one section rather than each
 * taking a band of its own. Locations a data cycle hides from the tree are appended as loose, so
 * no unplaced location is ever dropped.
 */
function buildSections(unplaced: readonly LocationOut[]): ArrangeSection[] {
  const ids = new Set(unplaced.map((l) => l.id));
  const rooted = unplaced.map((l) => (l.parent_id !== null && ids.has(l.parent_id) ? l : { ...l, parent_id: null }));
  const rows = flattenTree(rooted);

  const sections: ArrangeSection[] = [];
  let current: ArrangeSection | null = null;
  let loose: ArrangeSection | null = null;
  for (const [index, row] of rows.entries()) {
    const item = toItem(row.location);
    if (row.depth === 0) {
      const next = rows[index + 1];
      const isLeaf = next === undefined || next.depth === 0;
      if (isLeaf && row.location.kind !== "zone") {
        if (loose === null || sections[sections.length - 1] !== loose) {
          loose = { levels: [[]], band: false };
          sections.push(loose);
        }
        loose.levels[0]?.push(item);
        current = null;
      } else {
        current = { levels: [[item]], band: row.location.kind === "zone" };
        sections.push(current);
        loose = null;
      }
    } else if (current !== null) {
      while (current.levels.length <= row.depth) current.levels.push([]);
      current.levels[row.depth]?.push(item);
    }
  }

  const reached = new Set(rows.map((r) => r.location.id));
  const hidden = unplaced.filter((l) => !reached.has(l.id)).map((l) => ({ ...l, parent_id: null }));
  if (hidden.length > 0) {
    sections.push({ levels: [flattenTree(hidden).map((r) => toItem(r.location))], band: false });
  }
  return sections;
}

/** One row-group in reading order, wrapping at `rowLimit`. Coordinates are relative to its own
 *  top-left corner. */
function layoutLevel(items: readonly ArrangeItem[], rowLimit: number) {
  const tiles: PositionedTile[] = [];
  let x = 0;
  let y = 0;
  let lineDepth = 0;
  let width = 0;
  let previous: ArrangeItem | null = null;
  for (const item of items) {
    let gap = previous === null ? 0 : previous.parentId === item.parentId ? GAP_MM : AISLE_GAP_MM;
    if (x > 0 && x + gap + item.width > rowLimit) {
      y += lineDepth + GAP_MM;
      x = 0;
      lineDepth = 0;
      gap = 0;
    }
    x += gap;
    tiles.push({ item, x, y, width: item.width });
    x += item.width;
    lineDepth = Math.max(lineDepth, item.depth);
    width = Math.max(width, x);
    previous = item;
  }
  return { tiles, width, height: items.length === 0 ? 0 : y + lineDepth };
}

/** A section's tiles relative to its own top-left corner: its root, then each deeper row-group
 *  beneath. A band root is stretched to the section's full width. */
function layoutSection(section: ArrangeSection, rowLimit: number) {
  const layouts = section.levels.map((level) => layoutLevel(level, rowLimit));
  const width = layouts.reduce((widest, l) => Math.max(widest, l.width), 0);
  const tiles: PositionedTile[] = [];
  let y = 0;
  layouts.forEach((layout, depth) => {
    for (const tile of layout.tiles) {
      tiles.push({ ...tile, y: y + tile.y, width: section.band && depth === 0 ? width : tile.width });
    }
    y += layout.height + LEVEL_GAP_MM;
  });
  return { tiles, height: Math.max(0, y - LEVEL_GAP_MM) };
}

function layoutAll(sections: readonly ArrangeSection[], originX: number, originY: number, rowLimit: number) {
  const tiles: PositionedTile[] = [];
  let y = originY;
  for (const section of sections) {
    const laid = layoutSection(section, rowLimit);
    for (const tile of laid.tiles) tiles.push({ ...tile, x: originX + tile.x, y: y + tile.y });
    y += laid.height + SECTION_GAP_MM;
  }
  return tiles;
}

export interface ArrangeResult {
  /** New placements, in reading order — append these to the draft's own. */
  placements: EditorPlacement[];
  /** Locations that would sit beyond the server's coordinate bound, left unplaced. Only a floor
   *  whose existing layout already reaches the bound, or an absurd number of locations, gets any. */
  skipped: LocationOut[];
}

/**
 * Lays every location in `unplaced` out as a tidy, non-overlapping grid beneath whatever is
 * already `existing` on the floor — the "Auto-arrange" action. Pure and deterministic: the same
 * input always yields the same placements.
 *
 * - **Grouped by parent.** One section per zone (or other root that has locations beneath it),
 *   stacked top to bottom. A zone is a labelled band across the top of its section; its children
 *   sit in rows beneath it, one row-group per depth — racks, then shelves, then bins — with
 *   siblings of one parent kept together and a wider aisle between two parents' groups. Roots
 *   with nothing beneath them (a dock, a staging area, a loose bin) flow together in one row.
 * - **Sized by kind** (`footprintFor`), widened where a long code needs it.
 * - **Ordered** by `pick_sequence` (nulls last) then `code`, as the tree screen orders siblings.
 * - **Clear of what is there.** Everything starts below the lowest existing placement, aligned to
 *   the leftmost, so nothing can overlap it.
 * - **Within bounds.** Rows widen as the count grows so a big section stays roughly square;
 *   nothing is returned past `MAX_COORDINATE_MM` — what would not fit comes back as `skipped`.
 */
export function arrangeUnplaced(unplaced: readonly LocationOut[], existing: readonly EditorPlacement[]): ArrangeResult {
  if (unplaced.length === 0) return { placements: [], skipped: [] };

  const sections = buildSections(unplaced);
  const originX = existing.length === 0 ? MARGIN_MM : existing.reduce((least, p) => Math.min(least, p.x), Infinity);
  const originY =
    existing.length === 0 ? MARGIN_MM : existing.reduce((lowest, p) => Math.max(lowest, p.y + p.depth), 0) + SECTION_GAP_MM;

  const area = sections
    .flatMap((s) => s.levels.flat())
    .reduce((sum, item) => sum + (item.width + GAP_MM) * (item.depth + GAP_MM), 0);
  const rowLimit = Math.min(
    Math.max(ROW_MAX_WIDTH_MM, Math.ceil(Math.sqrt(area) / 1000) * 1000),
    MAX_COORDINATE_MM - originX,
  );

  const byId = new Map(unplaced.map((l) => [l.id, l]));
  const placements: EditorPlacement[] = [];
  const skipped: LocationOut[] = [];
  for (const tile of layoutAll(sections, originX, originY, rowLimit)) {
    const { location, depth } = tile.item;
    if (tile.x + tile.width <= MAX_COORDINATE_MM && tile.y + depth <= MAX_COORDINATE_MM) {
      placements.push({ locationId: location.id, x: tile.x, y: tile.y, z: null, width: tile.width, depth, height: null });
    } else {
      const original = byId.get(location.id);
      if (original) skipped.push(original);
    }
  }
  return { placements, skipped };
}

/** Beyond this many placements a new location goes beneath the lot rather than into a hole in
 *  it: the hole search is cubic and a floor this full has no holes worth the wait. */
const FREE_SPOT_SCAN_LIMIT = 150;

/**
 * Where one new `size` rectangle goes so that it is on screen and clear of every `existing`
 * placement: the first free spot reading top-left to bottom-right within the floor's current
 * width — typically the open end of a row — else a new row beneath everything. Candidates are
 * the edges of what is already there plus `GAP_MM`, so the result sits on the same grid as the
 * rest. Deterministic.
 */
export function findFreeSpot(existing: readonly EditorPlacement[], size: Footprint): { x: number; y: number } {
  if (existing.length === 0) return { x: MARGIN_MM, y: MARGIN_MM };

  const minX = existing.reduce((least, p) => Math.min(least, p.x), Infinity);
  const minY = existing.reduce((least, p) => Math.min(least, p.y), Infinity);
  const maxRight = existing.reduce((most, p) => Math.max(most, p.x + p.width), 0);
  const maxBottom = existing.reduce((most, p) => Math.max(most, p.y + p.depth), 0);
  const rightLimit = Math.min(minX + Math.max(ROW_MAX_WIDTH_MM, maxRight - minX), MAX_COORDINATE_MM);

  if (existing.length <= FREE_SPOT_SCAN_LIMIT) {
    const ascending = (values: number[]) => [...new Set(values)].sort((a, b) => a - b);
    const xs = ascending([minX, ...existing.flatMap((p) => [p.x, p.x + p.width + GAP_MM])]);
    const ys = ascending([minY, ...existing.flatMap((p) => [p.y, p.y + p.depth + GAP_MM])]);
    for (const y of ys) {
      if (y + size.depth > MAX_COORDINATE_MM) break;
      for (const x of xs) {
        if (x + size.width > rightLimit) break;
        const clear = existing.every(
          (p) =>
            x >= p.x + p.width + GAP_MM ||
            p.x >= x + size.width + GAP_MM ||
            y >= p.y + p.depth + GAP_MM ||
            p.y >= y + size.depth + GAP_MM,
        );
        if (clear) return { x, y };
      }
    }
  }
  return { x: minX, y: Math.min(maxBottom + SECTION_GAP_MM, MAX_COORDINATE_MM - size.depth) };
}
