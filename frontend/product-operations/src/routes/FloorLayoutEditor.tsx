import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { Link, useParams } from "react-router-dom";

import {
  computeWarehouseRoute,
  createLayoutVersion,
  discardLayoutVersion,
  getLayoutGeometry,
  getWarehouseFloor,
  listLayoutVersions,
  listLocations,
  publishLayoutVersion,
  saveLayoutGeometry,
  validateLayoutVersion,
} from "@/api/client";
import type {
  LayoutGeometryOut,
  LayoutValidationOut,
  LayoutVersionOut,
  LocationOut,
  RouteOut,
} from "@/api/types";
import { LayoutCanvasControls } from "@/floor/LayoutCanvasControls";
import { LayoutCanvasEmptyState } from "@/floor/LayoutCanvasEmptyState";
import { UnplacedLocationsCard } from "@/floor/UnplacedLocationsCard";
import type { ListFocusRequest } from "@/floor/UnplacedLocationsCard";
import { useCoarsePointer } from "@/floor/useCoarsePointer";
import { useElementSize } from "@/floor/useElementSize";
import { panCanvas } from "@/floor/panCanvas";
import { useLocationsOnOtherFloors } from "@/floor/useLocationsOnOtherFloors";
import { usePointerTap } from "@/floor/usePointerTap";
import { LOCATION_KIND_LABEL } from "@/data/locationKinds";
import type { EditorEdge, EditorNode, EditorPlacement, EditorState, ElsewhereLocation } from "@/lib/floorLayoutEditor";
import {
  CANVAS_ZOOM_STEPS,
  EMPTY_STATE,
  MAX_COORDINATE_MM,
  TOUCH_TARGET_PX,
  arrangeUnplaced,
  editorStateToGeometryIn,
  euclideanDistanceMm,
  findFreeSpot,
  floorTitle,
  footprintFor,
  geometryToEditorState,
  labelFontSizeMm,
  locationNodeCode,
  mmPerPixel,
  nextJunctionCode,
  pixelsToMm,
  splitByFloor,
  statesEqual,
  unplacedLocations,
} from "@/lib/floorLayoutEditor";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  Drawer,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  Select,
  Skeleton,
  Switch,
  Tag,
  TextField,
} from "@/ui";
import type { SelectOption } from "@/ui";
import { Announcer, ArrowRightIcon, MapPinIcon, NetworkIcon, PlusIcon, useAnnounce, useToast } from "@opsgrids/ui";
import { UnsavedChangesGuard } from "@opsgrids/ui";

const MIN_FOOTPRINT_MM = 200;
const MAX_FOOTPRINT_MM = 20_000;
/** Clear space around the plan inside the canvas, and the least extent it is ever drawn at — so one
 *  placed location is not blown up to fill the canvas. */
const CANVAS_PADDING_MM = 500;
const CANVAS_MIN_SIZE_MM = 4000;
const NO_LOCATIONS: LocationOut[] = [];
const NO_FLOORS: ReadonlyMap<string, string> = new Map();
/** What a resize handle and a walk node are drawn at on screen. Below the millimetre sizes they
 *  never go (a handle must not vanish on a big floor), above them only as far as the canvas is
 *  scaled down. */
const HANDLE_PX = 8;
const HANDLE_SELECTED_PX = 14;
const HANDLE_MIN_MM = 90;
const NODE_PX = 5;
const NODE_SELECTED_PX = 7;
const NODE_MIN_MM = 70;
const NODE_SELECTED_MIN_MM = 90;

type Mode = "select" | "add-node" | "connect";

type Selection =
  | { kind: "placement"; locationId: string }
  | { kind: "node"; code: string }
  | { kind: "edge"; fromCode: string; toCode: string }
  | null;

type Drag =
  | { kind: "placement"; locationId: string; offsetX: number; offsetY: number }
  | { kind: "resize"; locationId: string }
  | { kind: "node"; code: string; offsetX: number; offsetY: number }
  | null;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** What the last "Auto-arrange" did, kept while the draft is still exactly as it left it so the
 *  screen can say so and offer to take it back. */
interface ArrangeOutcome {
  before: EditorState;
  after: EditorState;
  placed: number;
  skipped: number;
}

function arrangeSummary({ placed, skipped }: ArrangeOutcome): string {
  const arranged = `Arranged ${placed} ${placed === 1 ? "location" : "locations"} in a grid.`;
  const overflow =
    skipped > 0
      ? ` ${skipped} would pass the floor's size limit, so ${skipped === 1 ? "it is" : "they are"} still unplaced.`
      : "";
  return `${arranged}${overflow} Walk nodes are not added; draw them where pickers walk.`;
}

/**
 * The floor layout editor (Fulfilment plan §2/F7) — a 2D canvas for placing, moving and resizing
 * a floor's locations, drawing the walk graph routing is computed over, saving that as a draft,
 * validating it, publishing it, and rolling back to a prior published version.
 *
 * Plain SVG rather than a canvas library: `react-three-fiber`/`drei`/`three` already in this app
 * are for the F9 3D viewer's own real-time rendering, not a fit for a 2D floor-plan editor with
 * a handful of draggable rectangles and lines — a heavier dependency here would buy nothing an
 * `<svg>` and pointer events don't already do.
 */
export function FloorLayoutEditorPage() {
  return (
    <Announcer>
      <FloorLayoutEditor />
    </Announcer>
  );
}

function FloorLayoutEditor() {
  const { id: warehouseId = "", floorId = "" } = useParams();
  const toast = useToast();
  const announce = useAnnounce();
  const canWrite = useCan("operations.warehouse.layout.write", "ORG");
  const canPublish = useCan("operations.warehouse.layout.publish", "ORG");

  const floorState = useAsync(() => getWarehouseFloor(floorId), [floorId]);
  const locationsState = useAsync(() => listLocations(warehouseId), [warehouseId]);
  const otherFloors = useLocationsOnOtherFloors(warehouseId, floorId);
  const versionsState = useAsync(() => listLayoutVersions(floorId), [floorId]);

  const draftVersion: LayoutVersionOut | null =
    versionsState.status === "success" ? (versionsState.data.find((v) => v.status === "draft") ?? null) : null;
  const publishedVersion: LayoutVersionOut | null =
    versionsState.status === "success"
      ? (versionsState.data.find((v) => v.status === "published") ?? null)
      : null;
  const activeVersion = draftVersion ?? publishedVersion;
  const isEditable = draftVersion !== null;

  const geometryState = useAsync(
    () => (activeVersion ? getLayoutGeometry(activeVersion.id) : Promise.resolve(null)),
    [activeVersion?.id],
  );

  const [state, setState] = useState<EditorState>(EMPTY_STATE);
  const [lastSaved, setLastSaved] = useState<EditorState>(EMPTY_STATE);
  const [validation, setValidation] = useState<LayoutValidationOut | null>(null);
  /** The last-loaded/-saved geometry's walk-node `id -> code` map — a validation issue names a
   *  node by its server id (`node_id`), and the canvas only ever knows nodes by `code`. */
  const [nodeCodeById, setNodeCodeById] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<Mode>("select");
  const [selection, setSelection] = useState<Selection>(null);
  const [connectFrom, setConnectFrom] = useState<string | null>(null);
  const [pendingEdge, setPendingEdge] = useState<{ fromCode: string; toCode: string } | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [discardConfirmOpen, setDiscardConfirmOpen] = useState(false);
  const [publishTarget, setPublishTarget] = useState<{ id: string; rollback: boolean } | null>(null);
  const [zoomIndex, setZoomIndex] = useState(0);
  const [touchEdit, setTouchEdit] = useState(false);
  const [unplacedOpen, setUnplacedOpen] = useState(true);
  const [lastArrange, setLastArrange] = useState<ArrangeOutcome | null>(null);
  /** A location another floor already holds, waiting on the answer to "place it here too?". */
  const [elsewhereTarget, setElsewhereTarget] = useState<ElsewhereLocation | null>(null);
  const [listFocus, setListFocus] = useState<ListFocusRequest | null>(null);
  /** The version id `state` was last loaded from — `null` for "no version" — or `undefined` before the first load. */
  const [hydratedFor, setHydratedFor] = useState<string | null | undefined>(undefined);
  const canvasRegionRef = useRef<HTMLDivElement>(null);
  const unplacedHeaderRef = useRef<HTMLButtonElement>(null);
  /** The location a Place just put on the canvas, to be brought into view once it is drawn. */
  const revealRef = useRef<string | null>(null);

  useEffect(() => {
    if (geometryState.status !== "success") return;
    // A load that is still the previous version's — the request for this one has not started or
    // finished — must not become this version's state.
    if (geometryState.data && geometryState.data.version.id !== activeVersion?.id) return;
    const next = geometryState.data ? geometryToEditorState(geometryState.data) : EMPTY_STATE;
    setState(next);
    setLastSaved(next);
    setSelection(null);
    setValidation(null);
    setNodeCodeById(
      geometryState.data ? Object.fromEntries(geometryState.data.walk_nodes.map((n) => [n.id, n.code])) : {},
    );
    setHydratedFor(geometryState.data?.version.id ?? null);
  }, [geometryState.status, geometryState.data, activeVersion?.id]);

  /** Whether `state` is the active version's own geometry. Until it is, the canvas is empty only
   *  because nothing has loaded — saying "no locations on this floor" then would be false, and a
   *  placement made then would be wiped by the load landing. */
  const editorReady = activeVersion !== null && hydratedFor === activeVersion.id;

  const isDirty = !statesEqual(state, lastSaved);

  const allLocations = locationsState.status === "success" ? locationsState.data : NO_LOCATIONS;
  const onOtherFloors = otherFloors.status === "success" ? otherFloors.data : NO_FLOORS;
  /** Locations on no floor and not on this draft — all that Auto-arrange and the list offer —
   *  apart from those another floor's published layout already holds. A location records its
   *  floor only when a layout is published, and publishing this floor with a location on it
   *  moves that location here, so the two must not be told apart by the locations alone. */
  const { unplaced, elsewhere } = useMemo(
    () => splitByFloor(unplacedLocations(allLocations, state), onOtherFloors),
    [allLocations, state, onOtherFloors],
  );
  /** Both loads must have landed before anything is offered: until the second does, a location
   *  that is on another floor cannot be told from one that is on none. */
  const placementStatus: "loading" | "error" | "success" =
    locationsState.status === "error" || otherFloors.status === "error"
      ? "error"
      : locationsState.status === "loading" || otherFloors.status === "loading"
        ? "loading"
        : "success";
  const placementProblem = locationsState.status === "error" ? "locations" : "floors";
  const placementError =
    locationsState.status === "error"
      ? problemMessage(locationsState.error)
      : otherFloors.status === "error"
        ? problemMessage(otherFloors.error)
        : undefined;
  function retryPlacementLoads() {
    if (locationsState.status === "error") locationsState.reload();
    if (otherFloors.status === "error") otherFloors.reload();
  }
  const locationById = useMemo(() => new Map(allLocations.map((l) => [l.id, l])), [allLocations]);
  /** An open draft and permission to write to it — who may place anything on the canvas. */
  const canPlace = isEditable && canWrite;
  /** A finger may drag the selected shape: Edit by touch is on, where there is a draft to edit. */
  const touchEditing = touchEdit && canPlace;
  /** The note about the last auto-arrange, for as long as nothing has been edited since. */
  const arrangeNote = lastArrange !== null && lastArrange.after === state ? lastArrange : null;
  const selectedPlacementId = selection?.kind === "placement" ? selection.locationId : null;
  const selectedNodeCode = selection?.kind === "node" ? selection.code : null;

  const openDraft = useMutation<void, LayoutVersionOut>(
    () => createLayoutVersion(floorId, { clone_from_published: true }),
    {
      onSuccess: () => {
        toast.success("Draft opened.");
        versionsState.reload();
      },
    },
  );

  const save = useMutation<void, LayoutGeometryOut>(
    () => {
      if (!activeVersion) throw new Error("No draft to save.");
      return saveLayoutGeometry(activeVersion.id, editorStateToGeometryIn(state));
    },
    {
      onSuccess: (result) => {
        const next = geometryToEditorState(result);
        setState(next);
        setLastSaved(next);
        setNodeCodeById(Object.fromEntries(result.walk_nodes.map((n) => [n.id, n.code])));
        toast.success("Draft saved.");
        void validate.mutate();
      },
    },
  );

  const validate = useMutation<void, LayoutValidationOut>(
    () => {
      if (!activeVersion) throw new Error("No draft to validate.");
      return validateLayoutVersion(activeVersion.id);
    },
    { onSuccess: (result) => setValidation(result) },
  );

  const discard = useMutation<void, void>(
    () => {
      if (!draftVersion) throw new Error("No draft to discard.");
      return discardLayoutVersion(draftVersion.id);
    },
    {
      onSuccess: () => {
        setDiscardConfirmOpen(false);
        toast.success("Draft discarded.");
        versionsState.reload();
      },
    },
  );

  const publish = useMutation((versionId: string) => publishLayoutVersion(versionId), {
    onSuccess: (result) => {
      toast.success(result.status === "published" ? "Layout published." : "Layout updated.");
      setPublishTarget(null);
      versionsState.reload();
    },
  });

  const flaggedLocationIds = useMemo(() => {
    const set = new Set<string>();
    for (const issue of validation?.issues ?? []) {
      for (const id of issue.location_ids) set.add(id);
    }
    return set;
  }, [validation]);

  const flaggedNodeCodes = useMemo(() => {
    const set = new Set<string>();
    for (const issue of validation?.issues ?? []) {
      if (issue.node_id) {
        const code = nodeCodeById[issue.node_id];
        if (code) set.add(code);
      }
    }
    return set;
  }, [validation, nodeCodeById]);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const { size: svgSize, ref: observeSvg } = useElementSize<SVGSVGElement>();
  const attachSvg = useCallback(
    (node: SVGSVGElement | null) => {
      svgRef.current = node;
      return observeSvg(node);
    },
    [observeSvg],
  );
  const coarsePointer = useCoarsePointer();
  const taps = usePointerTap();
  const [drag, setDrag] = useState<Drag>(null);
  /** The canvas's size as it was when a drag began. The canvas fits its content, so a shape pulled
   *  past the edge would rescale the view under the pointer, and the shape would run away from it. */
  const [dragBounds, setDragBounds] = useState<{ width: number; height: number } | null>(null);

  function toSvgPoint(clientX: number, clientY: number): { x: number; y: number } {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const point = svg.createSVGPoint();
    point.x = clientX;
    point.y = clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return { x: 0, y: 0 };
    const transformed = point.matrixTransform(ctm.inverse());
    return { x: transformed.x, y: transformed.y };
  }

  const fitBounds = useMemo(() => {
    let maxX = CANVAS_MIN_SIZE_MM;
    let maxY = CANVAS_MIN_SIZE_MM;
    for (const p of state.placements) {
      maxX = Math.max(maxX, p.x + p.width);
      maxY = Math.max(maxY, p.y + p.depth);
    }
    for (const n of state.nodes) {
      maxX = Math.max(maxX, n.x);
      maxY = Math.max(maxY, n.y);
    }
    return { width: maxX + CANVAS_PADDING_MM, height: maxY + CANVAS_PADDING_MM };
  }, [state]);
  const bounds = dragBounds ?? fitBounds;
  /** How many millimetres a screen pixel spans on the canvas, once it has been measured. */
  const mmPerPx = mmPerPixel(bounds, svgSize);

  /** A handle or walk node as it is drawn: the larger of a few pixels and its millimetre floor. */
  function drawnMm(pixels: number, floorMm: number): number {
    return pixelsToMm(pixels, mmPerPx, floorMm);
  }
  /** The side of the area that answers a press on something drawn `visibleMm` across. A mouse
   *  presses what it sees; a finger gets at least a touch target, wherever one is wanted. */
  function pressAreaMm(visibleMm: number, wantsTouchTarget: boolean): number {
    return wantsTouchTarget && coarsePointer ? Math.max(visibleMm, drawnMm(TOUCH_TARGET_PX, 0)) : visibleMm;
  }

  function startDrag(next: NonNullable<Drag>) {
    setDrag(next);
    setDragBounds(fitBounds);
  }
  const hasPlacements = state.placements.length > 0;
  const canvasHasContent = hasPlacements || state.nodes.length > 0;
  /** A canvas with no location on it is never zoomed: there is nothing to zoom to, and its message
   *  is centred on it. */
  const zoom = hasPlacements ? (CANVAS_ZOOM_STEPS[zoomIndex] ?? 1) : 1;
  /** Locations on the draft that no walk node stands at: pickers cannot be routed to them. */
  const placementsWithoutNode = useMemo(() => {
    const withNode = new Set(state.nodes.map((n) => n.locationId));
    return state.placements.filter((p) => !withNode.has(p.locationId)).length;
  }, [state]);

  function updatePlacement(locationId: string, patch: Partial<EditorPlacement>) {
    setState((prev) => ({
      ...prev,
      placements: prev.placements.map((p) => (p.locationId === locationId ? { ...p, ...patch } : p)),
    }));
  }

  function updateNode(code: string, patch: Partial<EditorNode>) {
    setState((prev) => ({
      ...prev,
      nodes: prev.nodes.map((n) => (n.code === code ? { ...n, ...patch } : n)),
    }));
  }

  function removePlacement(locationId: string) {
    setState((prev) => ({ ...prev, placements: prev.placements.filter((p) => p.locationId !== locationId) }));
    setSelection(null);
  }

  function removeNode(code: string) {
    setState((prev) => ({
      ...prev,
      nodes: prev.nodes.filter((n) => n.code !== code),
      edges: prev.edges.filter((e) => e.fromCode !== code && e.toCode !== code),
    }));
    setSelection(null);
  }

  function removeEdge(fromCode: string, toCode: string) {
    setState((prev) => ({
      ...prev,
      edges: prev.edges.filter((e) => !(e.fromCode === fromCode && e.toCode === toCode)),
    }));
    setSelection(null);
  }

  /** Brings the canvas to the person: focus, and a scroll that only moves the page as far as it
   *  must — on a phone the canvas is above the list a placement was made from. */
  function showCanvas() {
    const region = canvasRegionRef.current;
    if (!region) return;
    region.focus({ preventScroll: true });
    region.scrollIntoView?.({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }

  /** "Place one at a time": open the list and put focus on its header, so the next Tab is the
   *  first Place button. */
  function showUnplacedList() {
    setUnplacedOpen(true);
    const header = unplacedHeaderRef.current;
    if (!header) return;
    header.focus({ preventScroll: true });
    header.scrollIntoView?.({ block: "start", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }

  /** Puts one location on the canvas, selects it and brings it into view. Focus stays in the list
   *  it was placed from — on to the next Place button, or the header when none is left — so
   *  placing a run of locations by keyboard is one press each, not a trip across the page. */
  function placeLocation(location: LocationOut) {
    const { width, depth } = footprintFor(location.kind, location.code);
    const { x, y } = findFreeSpot(state.placements, { width, depth });
    const placement: EditorPlacement = { locationId: location.id, x, y, z: null, width, depth, height: null };
    const code = locationNodeCode(location.id);
    const alreadyHasNode = state.nodes.some((n) => n.code === code);
    const node: EditorNode | null = alreadyHasNode
      ? null
      : { code, locationId: location.id, x: x + width / 2, y: y + depth / 2, z: null };
    const listed = [...unplaced, ...elsewhere.map((entry) => entry.location)];
    const at = listed.findIndex((l) => l.id === location.id);
    const next = listed[at + 1] ?? listed[at - 1];
    setState((prev) => ({
      ...prev,
      placements: [...prev.placements, placement],
      nodes: node ? [...prev.nodes, node] : prev.nodes,
    }));
    setSelection({ kind: "placement", locationId: location.id });
    announce(`Placed ${location.code}`);
    revealRef.current = location.id;
    setListFocus((previous) => ({ locationId: next?.id ?? null, nonce: (previous?.nonce ?? 0) + 1 }));
  }

  /** Puts every unplaced location on the floor at once, as a grid beneath whatever is already
   *  there. Footprints only: a walk node is a decision about where pickers walk, and a node per
   *  location with no edges between them would fail the connectivity check on every one. Only
   *  locations on no floor: one that is already on another floor is placed here deliberately, one
   *  at a time, never as part of "all". */
  function arrangeAll() {
    if (placementStatus !== "success") return;
    const { placements, skipped } = arrangeUnplaced(unplaced, state.placements);
    if (placements.length === 0 && skipped.length === 0) return;
    const after: EditorState = { ...state, placements: [...state.placements, ...placements] };
    setState(after);
    setLastArrange({ before: state, after, placed: placements.length, skipped: skipped.length });
    setSelection(null);
    // Whatever zoom the plan was left at, the grid just added is beneath it — show all of it.
    setZoomIndex(0);
    announce(`Placed ${placements.length} ${placements.length === 1 ? "location" : "locations"}`);
    showCanvas();
  }

  function undoArrange() {
    if (!arrangeNote) return;
    setState(arrangeNote.before);
    setLastArrange(null);
    setSelection(null);
    setZoomIndex(0);
    announce(`Took ${arrangeNote.placed} ${arrangeNote.placed === 1 ? "location" : "locations"} off the floor`);
    showCanvas();
  }

  /** "Place here too" on a location another floor holds: asks first, because publishing this
   *  floor then puts the location on it as well as on the floor it is on now. */
  function placeElsewhereConfirmed() {
    if (!elsewhereTarget) return;
    placeLocation(elsewhereTarget.location);
    setElsewhereTarget(null);
  }

  useEffect(() => {
    const id = revealRef.current;
    if (id === null) return;
    revealRef.current = null;
    const shape = Array.from(svgRef.current?.querySelectorAll("[data-placement-id]") ?? []).find(
      (el) => el.getAttribute("data-placement-id") === id,
    );
    shape?.scrollIntoView?.({ block: "nearest", inline: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }, [state.placements]);

  function addJunctionAt(x: number, y: number) {
    const existing = new Set(state.nodes.map((n) => n.code));
    const code = nextJunctionCode(existing);
    setState((prev) => ({ ...prev, nodes: [...prev.nodes, { code, locationId: null, x, y, z: null }] }));
  }

  /** A finger or a pen: what the browser pans with, so neither can be told from a pan on press. */
  function isTouchLike(event: ReactPointerEvent<Element>): boolean {
    return event.pointerType === "touch" || event.pointerType === "pen";
  }

  /** Runs `act` on press for a mouse, and for a finger or pen only if it lifts without having
   *  moved: a touch that moves was a pan, and a pan must not select, add or connect anything. */
  function activate(event: ReactPointerEvent<Element>, act: () => void) {
    if (isTouchLike(event)) taps.begin(event, act);
    else act();
  }

  /** Whether pressing a shape starts dragging it. A mouse drags whatever it presses on an open
   *  draft. A finger or pen drags only the shape that is already selected, and only with Edit by
   *  touch on: anywhere else the same gesture pans the plan, and a tap selects. */
  function startsDrag(event: ReactPointerEvent<Element>, selected: boolean): boolean {
    if (mode !== "select" || !canPlace) return false;
    return !isTouchLike(event) || (touchEdit && selected);
  }

  function connectNode(code: string) {
    if (!connectFrom) {
      setConnectFrom(code);
    } else if (connectFrom !== code) {
      setPendingEdge({ fromCode: connectFrom, toCode: code });
      setConnectFrom(null);
    }
  }

  function handleCanvasPointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    if (event.target !== event.currentTarget) return; // a shape's own handler deals with itself
    if (mode === "add-node") {
      const point = toSvgPoint(event.clientX, event.clientY);
      activate(event, () => addJunctionAt(Math.round(point.x), Math.round(point.y)));
    } else if (mode === "select") {
      activate(event, () => setSelection(null));
    }
  }

  function handlePlacementPointerDown(event: ReactPointerEvent<SVGRectElement>, placement: EditorPlacement) {
    event.stopPropagation();
    const select = () => setSelection({ kind: "placement", locationId: placement.locationId });
    if (!startsDrag(event, selectedPlacementId === placement.locationId)) {
      activate(event, select);
      return;
    }
    const point = toSvgPoint(event.clientX, event.clientY);
    select();
    startDrag({ kind: "placement", locationId: placement.locationId, offsetX: point.x - placement.x, offsetY: point.y - placement.y });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handleResizePointerDown(event: ReactPointerEvent<SVGRectElement>, placement: EditorPlacement) {
    event.stopPropagation();
    if (mode !== "select" || !canPlace) return;
    const select = () => setSelection({ kind: "placement", locationId: placement.locationId });
    if (!startsDrag(event, selectedPlacementId === placement.locationId)) {
      activate(event, select);
      return;
    }
    select();
    startDrag({ kind: "resize", locationId: placement.locationId });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handleNodePointerDown(event: ReactPointerEvent<SVGCircleElement>, node: EditorNode) {
    event.stopPropagation();
    if (mode === "connect") {
      activate(event, () => connectNode(node.code));
      return;
    }
    const select = () => setSelection({ kind: "node", code: node.code });
    if (!startsDrag(event, selectedNodeCode === node.code)) {
      activate(event, select);
      return;
    }
    const point = toSvgPoint(event.clientX, event.clientY);
    select();
    startDrag({ kind: "node", code: node.code, offsetX: point.x - node.x, offsetY: point.y - node.y });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handleCanvasPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    if (!drag) {
      // A finger that is not dragging a shape is panning the plan — by script, as the canvas holds the touch.
      const travel = touchEditing ? taps.move(event) : null;
      if (travel) panCanvas(canvasRegionRef.current, travel.dx, travel.dy);
      return;
    }
    const point = toSvgPoint(event.clientX, event.clientY);
    if (drag.kind === "placement") {
      const placement = state.placements.find((p) => p.locationId === drag.locationId);
      if (!placement) return;
      updatePlacement(drag.locationId, {
        x: clamp(Math.round(point.x - drag.offsetX), 0, MAX_COORDINATE_MM),
        y: clamp(Math.round(point.y - drag.offsetY), 0, MAX_COORDINATE_MM),
      });
    } else if (drag.kind === "resize") {
      const placement = state.placements.find((p) => p.locationId === drag.locationId);
      if (!placement) return;
      updatePlacement(drag.locationId, {
        width: clamp(Math.round(point.x - placement.x), MIN_FOOTPRINT_MM, MAX_FOOTPRINT_MM),
        depth: clamp(Math.round(point.y - placement.y), MIN_FOOTPRINT_MM, MAX_FOOTPRINT_MM),
      });
    } else if (drag.kind === "node") {
      updateNode(drag.code, {
        x: clamp(Math.round(point.x - drag.offsetX), 0, MAX_COORDINATE_MM),
        y: clamp(Math.round(point.y - drag.offsetY), 0, MAX_COORDINATE_MM),
      });
    }
  }

  function handleCanvasPointerUp(event: ReactPointerEvent<SVGSVGElement>) {
    taps.end(event);
    setDrag(null);
    setDragBounds(null);
  }

  /** The browser took the gesture — it is panning — so nothing it began is a tap or a drag. */
  function handleCanvasPointerCancel() {
    taps.cancel();
    setDrag(null);
    setDragBounds(null);
  }

  const geometryFailed = activeVersion !== null && !editorReady && geometryState.status === "error";
  const loading =
    floorState.status === "loading" ||
    versionsState.status === "loading" ||
    (activeVersion !== null && !editorReady && !geometryFailed);

  if (floorState.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink warehouseId={warehouseId} />
        <Card className="p-6">
          <ErrorState title="Couldn't load this floor" message={problemMessage(floorState.error)} onRetry={floorState.reload} />
        </Card>
      </div>
    );
  }

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink warehouseId={warehouseId} />

      <PageHeader
        eyebrow="Layout"
        title={
          floorState.status === "success"
            ? floorTitle(floorState.data)
            : "Loading…"
        }
        description="Place locations, draw the walk graph a picker's route is computed over, save as a draft and publish when it's ready."
        actions={
          <>
            <Button variant="secondary" onClick={() => setHistoryOpen(true)}>
              Version history
            </Button>
            {activeVersion && !loading ? (
              <StatusTag status={isEditable ? "draft" : activeVersion.status} />
            ) : null}
          </>
        }
      />

      {loading ? (
        <Card className="p-6">
          <div className="flex flex-col gap-3">
            <Skeleton className="h-8 w-1/3" />
            <Skeleton className="h-64 w-full" />
          </div>
        </Card>
      ) : null}

      {!loading && !activeVersion ? (
        <Card className="p-6">
          <EmptyState
            icon={<MapPinIcon className="size-6" />}
            title="No layout yet"
            description="Open a draft to start placing this floor's locations and drawing its walk graph."
            action={
              <Can permission="operations.warehouse.layout.write" scope="ORG">
                <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => void openDraft.mutate()} disabled={openDraft.pending}>
                  {openDraft.pending ? "Opening…" : "Start editing"}
                </Button>
              </Can>
            }
          />
        </Card>
      ) : null}

      {!loading && geometryFailed ? (
        <Card className="p-6">
          <ErrorState
            title="Couldn't load this layout"
            message={geometryState.status === "error" ? problemMessage(geometryState.error) : ""}
            onRetry={geometryState.reload}
          />
        </Card>
      ) : null}

      {!loading && activeVersion && editorReady ? (
        <>
          {!isEditable ? (
            <InlineAlert
              tone="info"
              title="Viewing the published layout — read-only"
              action={
                <Can permission="operations.warehouse.layout.write" scope="ORG">
                  <Button size="sm" onClick={() => void openDraft.mutate()} disabled={openDraft.pending}>
                    {openDraft.pending ? "Opening…" : "Start editing"}
                  </Button>
                </Can>
              }
            >
              Locations move, and the walk graph changes, only on a draft. Start editing to open one from
              a copy of this layout.
            </InlineAlert>
          ) : null}

          {isEditable ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant={mode === "select" ? "primary" : "secondary"} onClick={() => setMode("select")}>
                Select / move
              </Button>
              <Button size="sm" variant={mode === "add-node" ? "primary" : "secondary"} onClick={() => setMode("add-node")}>
                Add walk node
              </Button>
              <Button
                size="sm"
                variant={mode === "connect" ? "primary" : "secondary"}
                onClick={() => {
                  setMode("connect");
                  setConnectFrom(null);
                }}
              >
                Connect nodes
              </Button>
              <span className="mx-2 h-6 w-px bg-line" aria-hidden />
              {canPlace ? (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={arrangeAll}
                  disabled={placementStatus !== "success" || unplaced.length === 0}
                >
                  Auto-arrange unplaced
                </Button>
              ) : null}
              <Button size="sm" variant="secondary" onClick={() => void validate.mutate()} disabled={validate.pending}>
                {validate.pending ? "Checking…" : "Check for problems"}
              </Button>
              <Button size="sm" onClick={() => void save.mutate()} disabled={save.pending || !isDirty}>
                {save.pending ? "Saving…" : "Save draft"}
              </Button>
              <Can permission="operations.warehouse.layout.publish" scope="ORG">
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() => draftVersion && setPublishTarget({ id: draftVersion.id, rollback: false })}
                  disabled={isDirty || validation === null || !validation.is_valid}
                >
                  Publish
                </Button>
              </Can>
              <Can permission="operations.warehouse.layout.write" scope="ORG">
                <Button size="sm" variant="destructive" onClick={() => setDiscardConfirmOpen(true)}>
                  Discard draft
                </Button>
              </Can>
            </div>
          ) : null}

          {isEditable && isDirty ? (
            <p className="text-caption text-muted">
              Unsaved changes. Save draft to keep them — Check for problems and Publish work from the saved draft.
            </p>
          ) : null}

          {isEditable && placementsWithoutNode > 0 ? (
            <p className="text-caption text-muted">
              {placementsWithoutNode === 1
                ? "1 location has no walk node, so a picker can't be routed to it."
                : `${placementsWithoutNode} locations have no walk node, so a picker can't be routed to them.`}{" "}
              Add walk nodes where pickers walk, and connect them.
            </p>
          ) : null}

          {validation && !validation.is_valid ? (
            <InlineAlert tone="warning" title={`${validation.issues.length} problem(s) found`}>
              Overlapping locations or a walk node with no traversable path must be fixed before this
              layout can be published.
              <ul className="mt-2 list-disc pl-4">
                {validation.issues.map((issue, i) => (
                  <li key={i}>{issue.message}</li>
                ))}
              </ul>
            </InlineAlert>
          ) : null}

          {save.error ? <InlineAlert tone="danger" title="Couldn't save">{problemMessage(save.error)}</InlineAlert> : null}
          {publish.error ? <InlineAlert tone="danger" title="Couldn't publish">{problemMessage(publish.error)}</InlineAlert> : null}

          <div className="flex flex-col gap-4 xl:flex-row">
            <Card className="flex flex-col gap-2 p-2 xl:min-w-0 xl:flex-1">
              {canvasHasContent ? (
                <LayoutCanvasControls
                  zoomIndex={zoomIndex}
                  onZoomIndex={setZoomIndex}
                  zoomable={hasPlacements}
                  touchEditable={canPlace}
                  touchEdit={touchEdit}
                  onTouchEdit={setTouchEdit}
                />
              ) : null}
              {arrangeNote ? (
                <div className="flex flex-wrap items-center justify-between gap-2 px-2 text-caption text-muted">
                  <p>{arrangeSummary(arrangeNote)}</p>
                  <Button size="sm" variant="ghost" onClick={undoArrange}>
                    Undo
                  </Button>
                </div>
              ) : null}
              {/* Zoomed, this is a scroller: it is a tab stop so the arrow keys can pan it, and only
                  then — at Fit there is nothing to scroll, and focus() can still land on it. */}
              <div
                ref={canvasRegionRef}
                tabIndex={zoom > 1 ? 0 : -1}
                role="group"
                aria-label={zoom > 1 ? "Floor layout, scrollable" : "Floor layout"}
                className="relative overflow-auto rounded-tile"
              >
                <svg
                  ref={attachSvg}
                  viewBox={`0 0 ${bounds.width} ${bounds.height}`}
                  // Edit by touch hands the whole canvas the touch, so a drag can move a shape; the canvas pans
                  // by script then (see panCanvas). Chrome ignores touch-action on shapes inside an SVG, so it
                  // cannot be given to the selected shape alone and leave the rest to the browser.
                  className={`block min-h-64 rounded-tile bg-sunken ${zoom === 1 ? "max-h-screen" : ""} ${
                    touchEditing ? "touch-none" : "touch-auto"
                  }`}
                  style={{ width: `${zoom * 100}%`, aspectRatio: `${bounds.width} / ${bounds.height}` }}
                  onPointerDown={handleCanvasPointerDown}
                  onPointerMove={handleCanvasPointerMove}
                  onPointerUp={handleCanvasPointerUp}
                  onPointerCancel={handleCanvasPointerCancel}
                  role="img"
                  aria-label="Floor layout canvas"
                >
                  {state.edges.map((edge) => {
                    const from = state.nodes.find((n) => n.code === edge.fromCode);
                    const to = state.nodes.find((n) => n.code === edge.toCode);
                    if (!from || !to) return null;
                    const isSelected =
                      selection?.kind === "edge" && selection.fromCode === edge.fromCode && selection.toCode === edge.toCode;
                    return (
                      <line
                        key={`${edge.fromCode}->${edge.toCode}`}
                        x1={from.x}
                        y1={from.y}
                        x2={to.x}
                        y2={to.y}
                        stroke={
                          edge.isTraversable
                            ? isSelected
                              ? "var(--color-accent)"
                              : "var(--color-positive)"
                            : "var(--color-negative)"
                        }
                        strokeWidth={isSelected ? 24 : 14}
                        strokeDasharray={edge.isTraversable ? undefined : "40 24"}
                        strokeLinecap="round"
                        className={mode === "select" ? "cursor-pointer" : undefined}
                        onPointerDown={(e) => {
                          e.stopPropagation();
                          if (mode === "select") {
                            activate(e, () => setSelection({ kind: "edge", fromCode: edge.fromCode, toCode: edge.toCode }));
                          }
                        }}
                      />
                    );
                  })}

                  {state.placements.map((placement) => {
                    const location = locationById.get(placement.locationId);
                    const label = location?.code ?? placement.locationId.slice(0, 6);
                    const isSelected = selectedPlacementId === placement.locationId;
                    const isFlagged = flaggedLocationIds.has(placement.locationId);
                    // A zone is the heading of its group, so it reads as a neutral band, not a storage tile.
                    const isBand = location?.kind === "zone";
                    // A handle is a few pixels wherever the plan is scaled down to fit, and never smaller than
                    // its millimetre floor nor larger than half the shape it sits on.
                    const handleSide = Math.min(
                      drawnMm(isSelected ? HANDLE_SELECTED_PX : HANDLE_PX, HANDLE_MIN_MM),
                      Math.max(HANDLE_MIN_MM, Math.floor(Math.min(placement.width, placement.depth) / 2)),
                    );
                    const handlePressSide = isSelected ? pressAreaMm(handleSide, true) : handleSide;
                    const handleCentre = {
                      x: placement.x + placement.width - handleSide / 2,
                      y: placement.y + placement.depth - handleSide / 2,
                    };
                    return (
                      <g key={placement.locationId}>
                        <rect
                          data-placement-id={placement.locationId}
                          x={placement.x}
                          y={placement.y}
                          width={placement.width}
                          height={placement.depth}
                          rx={40}
                          fill={
                            isFlagged ? "var(--color-negative-soft)" : isBand ? "var(--color-neutral-soft)" : "var(--color-info-soft)"
                          }
                          stroke={
                            isSelected
                              ? "var(--color-accent)"
                              : isFlagged
                                ? "var(--color-negative)"
                                : isBand
                                  ? "var(--color-field-edge)"
                                  : "var(--color-info)"
                          }
                          // Pixels, not millimetres: an outline that is a hairline at "Fit" on a phone is invisible.
                          strokeWidth={isSelected ? 3 : 1.5}
                          vectorEffect="non-scaling-stroke"
                          className={mode === "select" && canPlace ? "cursor-move" : "cursor-pointer"}
                          onPointerDown={(e) => handlePlacementPointerDown(e, placement)}
                        />
                        <text
                          x={placement.x + placement.width / 2}
                          y={placement.y + placement.depth / 2}
                          textAnchor="middle"
                          dominantBaseline="middle"
                          fontSize={labelFontSizeMm(placement.width, placement.depth, label)}
                          fontWeight={isBand ? 700 : 500}
                          fill="var(--color-ink)"
                          pointerEvents="none"
                        >
                          {label}
                        </text>
                        {mode === "select" && canPlace ? (
                          <>
                            {handlePressSide > handleSide ? (
                              <rect
                                x={handleCentre.x - handlePressSide / 2}
                                y={handleCentre.y - handlePressSide / 2}
                                width={handlePressSide}
                                height={handlePressSide}
                                fill="none"
                                pointerEvents="all"
                                className="cursor-nwse-resize"
                                onPointerDown={(e) => handleResizePointerDown(e, placement)}
                              />
                            ) : null}
                            <rect
                              x={handleCentre.x - handleSide / 2}
                              y={handleCentre.y - handleSide / 2}
                              width={handleSide}
                              height={handleSide}
                              fill="var(--color-accent)"
                              className="cursor-nwse-resize"
                              onPointerDown={(e) => handleResizePointerDown(e, placement)}
                            />
                          </>
                        ) : null}
                      </g>
                    );
                  })}

                  {state.nodes.map((node) => {
                    const isSelected = selectedNodeCode === node.code;
                    const isConnectSource = connectFrom === node.code;
                    const isFlagged = flaggedNodeCodes.has(node.code);
                    const emphasised = isSelected || isConnectSource;
                    const radius = drawnMm(
                      emphasised ? NODE_SELECTED_PX : NODE_PX,
                      emphasised ? NODE_SELECTED_MIN_MM : NODE_MIN_MM,
                    );
                    // Connecting is the job of the nodes themselves, so there every one gets a touch target;
                    // otherwise only the selected one does, which is the one a drag is aimed at.
                    const pressRadius = pressAreaMm(radius * 2, mode === "connect" || isSelected) / 2;
                    return (
                      <g key={node.code}>
                        {pressRadius > radius ? (
                          <circle
                            cx={node.x}
                            cy={node.y}
                            r={pressRadius}
                            fill="none"
                            pointerEvents="all"
                            className="cursor-pointer"
                            onPointerDown={(e) => handleNodePointerDown(e, node)}
                          />
                        ) : null}
                        <circle
                          cx={node.x}
                          cy={node.y}
                          r={radius}
                          fill={
                            isFlagged ? "var(--color-negative)" : node.locationId ? "var(--color-positive)" : "var(--color-neutral)"
                          }
                          stroke={emphasised ? "var(--color-accent)" : "var(--color-ink)"}
                          strokeWidth={12}
                          className="cursor-pointer"
                          onPointerDown={(e) => handleNodePointerDown(e, node)}
                        />
                      </g>
                    );
                  })}
                </svg>
                {!hasPlacements ? (
                  <LayoutCanvasEmptyState
                    warehouseId={warehouseId}
                    status={placementStatus}
                    problem={placementProblem}
                    totalLocations={allLocations.length}
                    unplacedCount={unplaced.length}
                    elsewhereCount={elsewhere.length}
                    canPlace={canPlace}
                    onArrange={arrangeAll}
                    onPlaceOneByOne={showUnplacedList}
                    onRetry={retryPlacementLoads}
                  />
                ) : null}
              </div>
            </Card>

            <div className="flex flex-col gap-4 xl:w-80 xl:shrink-0">
              {selection ? (
                <SelectionPanel
                  selection={selection}
                  state={state}
                  locations={locationsState.status === "success" ? locationsState.data : []}
                  editable={canWrite && isEditable}
                  onChangePlacement={updatePlacement}
                  onRemovePlacement={removePlacement}
                  onChangeNode={updateNode}
                  onRemoveNode={removeNode}
                  onChangeEdge={(fromCode, toCode, patch) =>
                    setState((prev) => ({
                      ...prev,
                      edges: prev.edges.map((e) => (e.fromCode === fromCode && e.toCode === toCode ? { ...e, ...patch } : e)),
                    }))
                  }
                  onRemoveEdge={removeEdge}
                />
              ) : null}

              {canPlace ? (
                <UnplacedLocationsCard
                  headerRef={unplacedHeaderRef}
                  locations={unplaced}
                  elsewhere={elsewhere}
                  totalLocations={allLocations.length}
                  status={placementStatus}
                  problem={placementProblem}
                  error={placementError}
                  onRetry={retryPlacementLoads}
                  open={unplacedOpen}
                  onToggle={() => setUnplacedOpen((open) => !open)}
                  onPlace={placeLocation}
                  onPlaceElsewhere={setElsewhereTarget}
                  focusRequest={listFocus}
                />
              ) : null}

              <RouteTester
                floorId={floorId}
                placements={state.placements}
                nodes={state.nodes}
                locations={locationsState.status === "success" ? locationsState.data : []}
                hasPublished={publishedVersion !== null}
              />
            </div>
          </div>
        </>
      ) : null}

      {pendingEdge ? (
        <EdgeCreateCard
          fromCode={pendingEdge.fromCode}
          toCode={pendingEdge.toCode}
          defaultDistance={(() => {
            const a = state.nodes.find((n) => n.code === pendingEdge.fromCode);
            const b = state.nodes.find((n) => n.code === pendingEdge.toCode);
            return a && b ? euclideanDistanceMm(a, b) : 1000;
          })()}
          onCancel={() => setPendingEdge(null)}
          onCreate={(edge) => {
            setState((prev) => ({
              ...prev,
              edges: [...prev.edges.filter((e) => !(e.fromCode === edge.fromCode && e.toCode === edge.toCode)), edge],
            }));
            setPendingEdge(null);
          }}
        />
      ) : null}

      <VersionHistoryDrawer
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        floorId={floorId}
        versions={versionsState.status === "success" ? versionsState.data : []}
        loading={versionsState.status === "loading"}
        canPublish={canPublish}
        onRollback={(versionId) => setPublishTarget({ id: versionId, rollback: true })}
      />

      <ConfirmDialog
        open={discardConfirmOpen}
        onClose={() => setDiscardConfirmOpen(false)}
        onConfirm={() => void discard.mutate()}
        title="Discard this draft?"
        body="Every change since this draft was opened is lost. The floor's published layout (if any) is unaffected."
        confirmLabel="Discard draft"
        pendingLabel="Discarding…"
        pending={discard.pending}
        {...(discard.error ? { error: problemMessage(discard.error) } : {})}
      />

      <ConfirmDialog
        open={elsewhereTarget !== null}
        onClose={() => setElsewhereTarget(null)}
        onConfirm={placeElsewhereConfirmed}
        title={elsewhereTarget ? `Place ${elsewhereTarget.location.code} on this floor too?` : "Place on this floor too?"}
        body={
          elsewhereTarget
            ? `${elsewhereTarget.location.code} is already on ${elsewhereTarget.floorTitle}'s published layout. Publishing this layout with it on puts it on this floor as well: Find stock and put-away guidance show it on whichever of the two floors was published last, and ${elsewhereTarget.floorTitle} keeps listing it until it is removed there.`
            : ""
        }
        confirmLabel="Place here too"
        tone="primary"
      />

      <ConfirmDialog
        open={publishTarget !== null}
        onClose={() => setPublishTarget(null)}
        onConfirm={() => publishTarget && void publish.mutate(publishTarget.id)}
        title={publishTarget?.rollback ? "Roll back to this version?" : "Publish this layout?"}
        body={
          publishTarget?.rollback
            ? "This becomes the floor's current layout again. The layout published right now is archived, not deleted — you can roll back to it later too."
            : "This becomes the floor's current layout — every worker's pick routing and the 3D viewer read whatever is published. The layout published right now (if any) is archived, not deleted."
        }
        confirmLabel={publishTarget?.rollback ? "Roll back" : "Publish"}
        pendingLabel={publishTarget?.rollback ? "Rolling back…" : "Publishing…"}
        pending={publish.pending}
        tone="primary"
        {...(publish.error ? { error: problemMessage(publish.error) } : {})}
      />

      <UnsavedChangesGuard when={isDirty} subject="this floor's layout" />
    </div>
  );
}

function StatusTag({ status }: { status: "draft" | "published" | "archived" }) {
  if (status === "draft") return <Tag tone="info">Editing draft</Tag>;
  if (status === "published") return <Tag tone="positive">Published</Tag>;
  return <Tag tone="neutral">Archived</Tag>;
}

function BackLink({ warehouseId }: { warehouseId: string }) {
  return (
    <Link
      to={`/warehouses/${warehouseId}/floors`}
      className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to floors
    </Link>
  );
}

interface SelectionPanelProps {
  selection: NonNullable<Selection>;
  state: EditorState;
  locations: LocationOut[];
  editable: boolean;
  onChangePlacement: (locationId: string, patch: Partial<EditorPlacement>) => void;
  onRemovePlacement: (locationId: string) => void;
  onChangeNode: (code: string, patch: Partial<EditorNode>) => void;
  onRemoveNode: (code: string) => void;
  onChangeEdge: (fromCode: string, toCode: string, patch: Partial<EditorEdge>) => void;
  onRemoveEdge: (fromCode: string, toCode: string) => void;
}

function numberField(value: number, onChange: (n: number) => void, label: string, disabled: boolean) {
  return (
    <TextField
      label={label}
      inputMode="numeric"
      value={String(value)}
      disabled={disabled}
      onChange={(e) => {
        const n = Number(e.target.value);
        if (Number.isFinite(n)) onChange(Math.round(n));
      }}
    />
  );
}

function SelectionPanel({
  selection,
  state,
  locations,
  editable,
  onChangePlacement,
  onRemovePlacement,
  onChangeNode,
  onRemoveNode,
  onChangeEdge,
  onRemoveEdge,
}: SelectionPanelProps) {
  if (selection.kind === "placement") {
    const placement = state.placements.find((p) => p.locationId === selection.locationId);
    if (!placement) return null;
    const location = locations.find((l) => l.id === placement.locationId);
    return (
      <Card className="flex flex-col gap-3 p-4">
        <h2 className="text-section">{location?.code ?? "Location"}</h2>
        {location ? <p className="text-sm text-ink-soft">{LOCATION_KIND_LABEL[location.kind]}</p> : null}
        <div className="grid grid-cols-2 gap-3">
          {numberField(placement.x, (n) => onChangePlacement(placement.locationId, { x: n }), "X (mm)", !editable)}
          {numberField(placement.y, (n) => onChangePlacement(placement.locationId, { y: n }), "Y (mm)", !editable)}
          {numberField(placement.width, (n) => onChangePlacement(placement.locationId, { width: n }), "Width (mm)", !editable)}
          {numberField(placement.depth, (n) => onChangePlacement(placement.locationId, { depth: n }), "Depth (mm)", !editable)}
        </div>
        {editable ? (
          <Button size="sm" variant="destructive" onClick={() => onRemovePlacement(placement.locationId)}>
            Remove from floor
          </Button>
        ) : null}
      </Card>
    );
  }

  if (selection.kind === "node") {
    const node = state.nodes.find((n) => n.code === selection.code);
    if (!node) return null;
    return (
      <Card className="flex flex-col gap-3 p-4">
        <h2 className="text-section">Walk node {node.code}</h2>
        <p className="text-sm text-ink-soft">{node.locationId ? "At a location" : "Junction"}</p>
        <div className="grid grid-cols-2 gap-3">
          {numberField(node.x, (n) => onChangeNode(node.code, { x: n }), "X (mm)", !editable)}
          {numberField(node.y, (n) => onChangeNode(node.code, { y: n }), "Y (mm)", !editable)}
        </div>
        {editable ? (
          <Button size="sm" variant="destructive" onClick={() => onRemoveNode(node.code)}>
            Delete node
          </Button>
        ) : null}
      </Card>
    );
  }

  const edge = state.edges.find((e) => e.fromCode === selection.fromCode && e.toCode === selection.toCode);
  if (!edge) return null;
  return (
    <Card className="flex flex-col gap-3 p-4">
      <h2 className="text-section">
        {edge.fromCode} → {edge.toCode}
      </h2>
      {numberField(edge.distanceMm, (n) => onChangeEdge(edge.fromCode, edge.toCode, { distanceMm: n }), "Distance (mm)", !editable)}
      <Switch
        checked={edge.isTraversable}
        onChange={(checked) => onChangeEdge(edge.fromCode, edge.toCode, { isTraversable: checked })}
        label="Traversable"
        description={edge.isTraversable ? "Open for routing." : "Blocked — excluded from routing."}
        disabled={!editable}
      />
      <Switch
        checked={edge.bidirectional}
        onChange={(checked) => onChangeEdge(edge.fromCode, edge.toCode, { bidirectional: checked })}
        label="Two-way"
        description={edge.bidirectional ? "Walkable in both directions." : `One-way, ${edge.fromCode} → ${edge.toCode} only.`}
        disabled={!editable}
      />
      {editable ? (
        <Button size="sm" variant="destructive" onClick={() => onRemoveEdge(edge.fromCode, edge.toCode)}>
          Delete edge
        </Button>
      ) : null}
    </Card>
  );
}

function EdgeCreateCard({
  fromCode,
  toCode,
  defaultDistance,
  onCancel,
  onCreate,
}: {
  fromCode: string;
  toCode: string;
  defaultDistance: number;
  onCancel: () => void;
  onCreate: (edge: EditorEdge) => void;
}) {
  const [distance, setDistance] = useState(String(defaultDistance));
  const [traversable, setTraversable] = useState(true);
  const [bidirectional, setBidirectional] = useState(true);

  return (
    <Card className="flex flex-col gap-3 p-4">
      <h2 className="text-section">
        New edge: {fromCode} → {toCode}
      </h2>
      <TextField
        label="Distance (mm)"
        inputMode="numeric"
        value={distance}
        onChange={(e) => setDistance(e.target.value)}
        helper="Defaults to the straight-line distance between the two nodes."
      />
      <Switch checked={traversable} onChange={setTraversable} label="Traversable" />
      <Switch checked={bidirectional} onChange={setBidirectional} label="Two-way" />
      <div className="flex gap-2">
        <Button
          size="sm"
          onClick={() => {
            const n = Number(distance);
            onCreate({
              fromCode,
              toCode,
              distanceMm: Number.isFinite(n) && n > 0 ? Math.round(n) : defaultDistance,
              isTraversable: traversable,
              bidirectional,
            });
          }}
        >
          Add edge
        </Button>
        <Button size="sm" variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </Card>
  );
}

function VersionHistoryDrawer({
  open,
  onClose,
  versions,
  loading,
  canPublish,
  onRollback,
}: {
  open: boolean;
  onClose: () => void;
  floorId: string;
  versions: LayoutVersionOut[];
  loading: boolean;
  canPublish: boolean;
  onRollback: (versionId: string) => void;
}) {
  return (
    <Drawer open={open} onClose={onClose} title="Version history" description="Draft, published and archived — nothing is ever deleted once published.">
      {loading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {versions.map((v) => (
            <li key={v.id} className="flex items-center justify-between gap-2 rounded-tile border border-line p-3">
              <div className="flex flex-col gap-1">
                <span className="text-sm font-semibold text-ink">Version {v.version_number}</span>
                <StatusTag status={v.status} />
                {v.published_at ? (
                  <span className="text-xs text-faint">Published {new Date(v.published_at).toLocaleString()}</span>
                ) : null}
              </div>
              {v.status === "archived" && canPublish ? (
                <Button size="sm" variant="secondary" onClick={() => onRollback(v.id)}>
                  Roll back
                </Button>
              ) : null}
            </li>
          ))}
          {versions.length === 0 ? <li className="text-sm text-muted">No versions yet.</li> : null}
        </ul>
      )}
    </Drawer>
  );
}

function RouteTester({
  floorId,
  placements,
  nodes,
  locations,
  hasPublished,
}: {
  floorId: string;
  placements: EditorPlacement[];
  nodes: EditorNode[];
  locations: LocationOut[];
  hasPublished: boolean;
}) {
  const [fromId, setFromId] = useState("");
  const [toId, setToId] = useState("");
  const [result, setResult] = useState<RouteOut | null>(null);

  const route = useMutation<void, RouteOut>(() => computeWarehouseRoute(floorId, fromId, toId), {
    onSuccess: (r) => setResult(r),
  });

  // A route runs between walk nodes: a location with none cannot be routed to, so it is not offered.
  const withNode = new Set(nodes.map((n) => n.locationId));
  const routable = placements.filter((p) => withNode.has(p.locationId));
  const options: SelectOption[] = routable
    .map((p) => locations.find((l) => l.id === p.locationId))
    .filter((l): l is LocationOut => l !== undefined)
    .map((l) => ({ value: l.id, label: `${l.code} — ${LOCATION_KIND_LABEL[l.kind]}` }));
  const unroutable = placements.length - routable.length;

  if (!hasPublished) {
    return (
      <Card className="flex flex-col gap-2 p-4">
        <h2 className="text-section">Try a route</h2>
        <p className="text-sm text-muted">Publish a layout first — routing is always computed against what is published.</p>
      </Card>
    );
  }

  return (
    <Card className="flex flex-col gap-3 p-4">
      <h2 className="flex items-center gap-2 text-section">
        <NetworkIcon className="size-4" /> Try a route
      </h2>
      <p className="text-xs text-muted">Computed against the published layout, not this draft.</p>
      {unroutable > 0 ? (
        <p className="text-xs text-muted">
          {unroutable === 1
            ? "1 location is not listed: it has no walk node."
            : `${unroutable} locations are not listed: they have no walk node.`}
        </p>
      ) : null}
      <Select label="From" options={options} value={fromId} onChange={(e) => setFromId(e.target.value)} placeholder="Choose a location" />
      <Select label="To" options={options} value={toId} onChange={(e) => setToId(e.target.value)} placeholder="Choose a location" />
      <Button size="sm" onClick={() => void route.mutate()} disabled={!fromId || !toId || route.pending}>
        {route.pending ? "Computing…" : "Compute route"}
      </Button>
      {route.error ? <InlineAlert tone="warning" title="No route">{problemMessage(route.error)}</InlineAlert> : null}
      {result ? (
        <p className="text-sm text-ink">
          {result.node_ids.length} node(s), {result.distance_mm.toLocaleString()}mm total.
        </p>
      ) : null}
    </Card>
  );
}
