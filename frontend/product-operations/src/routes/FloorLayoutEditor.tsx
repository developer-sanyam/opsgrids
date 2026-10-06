import { useEffect, useMemo, useRef, useState } from "react";
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
import { LOCATION_KIND_LABEL } from "@/data/locationKinds";
import type { EditorEdge, EditorNode, EditorPlacement, EditorState } from "@/lib/floorLayoutEditor";
import {
  CANVAS_ZOOM_STEPS,
  EMPTY_STATE,
  MAX_COORDINATE_MM,
  arrangeUnplaced,
  editorStateToGeometryIn,
  euclideanDistanceMm,
  findFreeSpot,
  footprintFor,
  geometryToEditorState,
  labelFontSizeMm,
  locationNodeCode,
  nextJunctionCode,
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
  /** The version id `state` was last loaded from — `null` for "no version" — or `undefined` before the first load. */
  const [hydratedFor, setHydratedFor] = useState<string | null | undefined>(undefined);
  const canvasRegionRef = useRef<HTMLDivElement>(null);
  const unplacedHeaderRef = useRef<HTMLButtonElement>(null);

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
  const unplaced = useMemo(() => unplacedLocations(allLocations, state), [allLocations, state]);
  const locationById = useMemo(() => new Map(allLocations.map((l) => [l.id, l])), [allLocations]);
  /** An open draft and permission to write to it — who may place anything on the canvas. */
  const canPlace = isEditable && canWrite;
  /** The note about the last auto-arrange, for as long as nothing has been edited since. */
  const arrangeNote = lastArrange !== null && lastArrange.after === state ? lastArrange : null;

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

  const svgRef = useRef<SVGSVGElement>(null);
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

  function startDrag(next: NonNullable<Drag>) {
    setDrag(next);
    setDragBounds(fitBounds);
  }
  const canvasHasContent = state.placements.length > 0 || state.nodes.length > 0;
  /** An empty canvas is never zoomed: there is nothing to zoom to, and its message is centred on it. */
  const zoom = canvasHasContent ? (CANVAS_ZOOM_STEPS[zoomIndex] ?? 1) : 1;

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

  function placeLocation(location: LocationOut) {
    const { width, depth } = footprintFor(location.kind, location.code);
    const { x, y } = findFreeSpot(state.placements, { width, depth });
    const placement: EditorPlacement = { locationId: location.id, x, y, z: null, width, depth, height: null };
    const code = locationNodeCode(location.id);
    const alreadyHasNode = state.nodes.some((n) => n.code === code);
    const node: EditorNode | null = alreadyHasNode
      ? null
      : { code, locationId: location.id, x: x + width / 2, y: y + depth / 2, z: null };
    setState((prev) => ({
      ...prev,
      placements: [...prev.placements, placement],
      nodes: node ? [...prev.nodes, node] : prev.nodes,
    }));
    setSelection({ kind: "placement", locationId: location.id });
    announce(`Placed ${location.code}`);
    showCanvas();
  }

  /** Puts every unplaced location on the floor at once, as a grid beneath whatever is already
   *  there. Footprints only: a walk node is a decision about where pickers walk, and a node per
   *  location with no edges between them would fail the connectivity check on every one. */
  function arrangeAll() {
    const { placements, skipped } = arrangeUnplaced(unplaced, state.placements);
    if (placements.length === 0 && skipped.length === 0) return;
    const after: EditorState = { ...state, placements: [...state.placements, ...placements] };
    setState(after);
    setLastArrange({ before: state, after, placed: placements.length, skipped: skipped.length });
    setSelection(null);
    announce(`Placed ${placements.length} ${placements.length === 1 ? "location" : "locations"}`);
    showCanvas();
  }

  function undoArrange() {
    if (!arrangeNote) return;
    setState(arrangeNote.before);
    setLastArrange(null);
    setSelection(null);
    announce(`Took ${arrangeNote.placed} ${arrangeNote.placed === 1 ? "location" : "locations"} off the floor`);
    showCanvas();
  }

  function addJunctionAt(x: number, y: number) {
    const existing = new Set(state.nodes.map((n) => n.code));
    const code = nextJunctionCode(existing);
    setState((prev) => ({ ...prev, nodes: [...prev.nodes, { code, locationId: null, x, y, z: null }] }));
  }

  /** A touch while "Edit by touch" is off belongs to the page — it scrolls — so it selects and
   *  does nothing else: no drag, no new walk node. A mouse or a pen is never held back. */
  function isScrollTouch(event: ReactPointerEvent<Element>): boolean {
    return event.pointerType === "touch" && !touchEdit;
  }

  function handleCanvasPointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    if (event.target !== event.currentTarget) return; // a shape's own handler deals with itself
    if (mode === "add-node") {
      if (isScrollTouch(event)) return;
      const point = toSvgPoint(event.clientX, event.clientY);
      addJunctionAt(Math.round(point.x), Math.round(point.y));
    } else if (mode === "select") {
      setSelection(null);
    }
  }

  function handlePlacementPointerDown(event: ReactPointerEvent<SVGRectElement>, placement: EditorPlacement) {
    event.stopPropagation();
    if (mode !== "select" || !canWrite || !isEditable || isScrollTouch(event)) {
      setSelection({ kind: "placement", locationId: placement.locationId });
      return;
    }
    const point = toSvgPoint(event.clientX, event.clientY);
    setSelection({ kind: "placement", locationId: placement.locationId });
    startDrag({ kind: "placement", locationId: placement.locationId, offsetX: point.x - placement.x, offsetY: point.y - placement.y });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handleResizePointerDown(event: ReactPointerEvent<SVGRectElement>, placement: EditorPlacement) {
    event.stopPropagation();
    if (mode !== "select" || !canWrite || !isEditable || isScrollTouch(event)) return;
    setSelection({ kind: "placement", locationId: placement.locationId });
    startDrag({ kind: "resize", locationId: placement.locationId });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handleNodePointerDown(event: ReactPointerEvent<SVGCircleElement>, node: EditorNode) {
    event.stopPropagation();
    if (mode === "connect") {
      if (isScrollTouch(event)) return;
      if (!connectFrom) {
        setConnectFrom(node.code);
      } else if (connectFrom !== node.code) {
        setPendingEdge({ fromCode: connectFrom, toCode: node.code });
        setConnectFrom(null);
      }
      return;
    }
    if (mode !== "select" || !canWrite || !isEditable || isScrollTouch(event)) {
      setSelection({ kind: "node", code: node.code });
      return;
    }
    const point = toSvgPoint(event.clientX, event.clientY);
    setSelection({ kind: "node", code: node.code });
    startDrag({ kind: "node", code: node.code, offsetX: point.x - node.x, offsetY: point.y - node.y });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handleCanvasPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    if (!drag) return;
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

  function handleCanvasPointerUp() {
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
            ? `Floor ${floorState.data.floor_number}${floorState.data.name ? ` — ${floorState.data.name}` : ""}`
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
                <Button size="sm" variant="secondary" onClick={arrangeAll} disabled={unplaced.length === 0}>
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
              <div
                ref={canvasRegionRef}
                tabIndex={-1}
                role="group"
                aria-label="Floor layout"
                className="relative overflow-auto rounded-tile"
              >
                <svg
                  ref={svgRef}
                  viewBox={`0 0 ${bounds.width} ${bounds.height}`}
                  className={`block min-h-64 rounded-tile bg-sunken ${zoom === 1 ? "max-h-screen" : ""} ${
                    touchEdit && canPlace ? "touch-none" : "touch-auto"
                  }`}
                  style={{ width: `${zoom * 100}%`, aspectRatio: `${bounds.width} / ${bounds.height}` }}
                  onPointerDown={handleCanvasPointerDown}
                  onPointerMove={handleCanvasPointerMove}
                  onPointerUp={handleCanvasPointerUp}
                  onPointerCancel={handleCanvasPointerUp}
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
                          if (mode === "select") setSelection({ kind: "edge", fromCode: edge.fromCode, toCode: edge.toCode });
                        }}
                      />
                    );
                  })}

                  {state.placements.map((placement) => {
                    const location = locationById.get(placement.locationId);
                    const label = location?.code ?? placement.locationId.slice(0, 6);
                    const isSelected = selection?.kind === "placement" && selection.locationId === placement.locationId;
                    const isFlagged = flaggedLocationIds.has(placement.locationId);
                    // A zone is the heading of its group, so it reads as a neutral band, not a storage tile.
                    const isBand = location?.kind === "zone";
                    return (
                      <g key={placement.locationId}>
                        <rect
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
                          className={mode === "select" && canWrite && isEditable ? "cursor-move" : "cursor-pointer"}
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
                        {mode === "select" && canWrite && isEditable ? (
                          <rect
                            x={placement.x + placement.width - 90}
                            y={placement.y + placement.depth - 90}
                            width={90}
                            height={90}
                            fill="var(--color-accent)"
                            className="cursor-nwse-resize"
                            onPointerDown={(e) => handleResizePointerDown(e, placement)}
                          />
                        ) : null}
                      </g>
                    );
                  })}

                  {state.nodes.map((node) => {
                    const isSelected = selection?.kind === "node" && selection.code === node.code;
                    const isConnectSource = connectFrom === node.code;
                    const isFlagged = flaggedNodeCodes.has(node.code);
                    return (
                      <circle
                        key={node.code}
                        cx={node.x}
                        cy={node.y}
                        r={isSelected || isConnectSource ? 90 : 70}
                        fill={
                          isFlagged ? "var(--color-negative)" : node.locationId ? "var(--color-positive)" : "var(--color-neutral)"
                        }
                        stroke={isSelected || isConnectSource ? "var(--color-accent)" : "var(--color-ink)"}
                        strokeWidth={12}
                        className="cursor-pointer"
                        onPointerDown={(e) => handleNodePointerDown(e, node)}
                      />
                    );
                  })}
                </svg>
                {!canvasHasContent ? (
                  <LayoutCanvasEmptyState
                    warehouseId={warehouseId}
                    locations={locationsState.status}
                    totalLocations={allLocations.length}
                    unplacedCount={unplaced.length}
                    canPlace={canPlace}
                    onArrange={arrangeAll}
                    onPlaceOneByOne={showUnplacedList}
                    onRetry={locationsState.reload}
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
                  totalLocations={allLocations.length}
                  status={locationsState.status}
                  error={locationsState.status === "error" ? problemMessage(locationsState.error) : undefined}
                  onRetry={locationsState.reload}
                  open={unplacedOpen}
                  onToggle={() => setUnplacedOpen((open) => !open)}
                  onPlace={placeLocation}
                />
              ) : null}

              <RouteTester floorId={floorId} placements={state.placements} locations={locationsState.status === "success" ? locationsState.data : []} hasPublished={publishedVersion !== null} />
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
      className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
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
  locations,
  hasPublished,
}: {
  floorId: string;
  placements: EditorPlacement[];
  locations: LocationOut[];
  hasPublished: boolean;
}) {
  const [fromId, setFromId] = useState("");
  const [toId, setToId] = useState("");
  const [result, setResult] = useState<RouteOut | null>(null);

  const route = useMutation<void, RouteOut>(() => computeWarehouseRoute(floorId, fromId, toId), {
    onSuccess: (r) => setResult(r),
  });

  const options: SelectOption[] = placements
    .map((p) => locations.find((l) => l.id === p.locationId))
    .filter((l): l is LocationOut => l !== undefined)
    .map((l) => ({ value: l.id, label: `${l.code} — ${LOCATION_KIND_LABEL[l.kind]}` }));

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
