import { Suspense, useEffect, useMemo, useState } from "react";
import type { ComponentProps, ReactNode } from "react";

import { listLocations } from "@/api/client";
import type { LocationKind, PublishedLayoutOut } from "@/api/types";
import { LOCATION_KIND_LABEL } from "@/data/locationKinds";
import { reportOnce } from "@/lib/clientReport";
import { normalizePlacements, placementsToSceneNodes } from "@/lib/floorGeometry";
import { computeWarehouseLayout } from "@/lib/warehouseLayout";
import type { WarehouseLayoutNode } from "@/lib/warehouseLayout";
import { hasWebGLSupport } from "@/lib/webgl";
import { problemMessage, useAsync } from "@opsgrids/client";
import { Card, EmptyState, ErrorState, InlineAlert, Skeleton } from "@/ui";
import { PanelBoundary } from "@/ui/PanelBoundary";
import { retryableLazy } from "@/ui/retryableLazy";
import { useSlowLoad } from "@/lib/useSlowLoad";
import { SlowLoadNotice } from "@/ui/SlowLoadNotice";
import { BoxIcon } from "@opsgrids/ui";
import type { BinSummary } from "./binSummary";
import { SceneLegend } from "./SceneLegend";

// `three`, `@react-three/fiber` and `@react-three/drei` arrive only once WebGL is confirmed and
// this panel is on screen — a browser without WebGL, or a user who never opens a 3-D view, never
// downloads them. Retryable, because a chunk that fails to download must be asked for again when
// the user presses "Try again", not remembered as failed.
type SceneProps = ComponentProps<typeof import("@/routes/Warehouse3DScene").Warehouse3DScene>;
const scene = retryableLazy<SceneProps>(() =>
  import("@/routes/Warehouse3DScene").then((m) => ({ default: m.Warehouse3DScene })),
);
const Warehouse3DScene = scene.Panel;

interface Floor3DPanelProps {
  warehouseId: string;
  /** The floor being viewed, or `null` for the warehouse-wide auto-arranged view. */
  floorId: string | null;
  /** That floor's published layout, once loaded; `null` while there is no floor or nothing to show. */
  published: PublishedLayoutOut | null;
  highlightIds: ReadonlySet<string>;
  /** The highlighted location is known to be missing from a published layout that exists. It only
   *  changes what the auto-arranged banner says, so the banner never claims there is no floor plan. */
  highlightUnplaced?: boolean | undefined;
  selectedId: string | null;
  onSelect: (bin: BinSummary) => void;
  /** What to offer when the 3-D view cannot be shown here: a way to the 2-D view or the list. */
  unavailableAction: ReactNode;
}

/**
 * The 3-D view of one floor, in whichever of its two honest forms applies.
 *
 * A floor with a **published layout** is drawn from those placements — millimetres from the layout
 * editor, in metres here — and nothing on the screen says the layout is approximate, because it is
 * not. A floor with **no published layout** (or no floor chosen at all) keeps the auto-arranged
 * view computed from the location tree, with the banner that says exactly that. The two never mix:
 * a draft never reaches either, since the server only ever returns the published version.
 *
 * Every failure is local to this panel: no WebGL, a scene that throws, locations that will not
 * load. Each is a message with a way forward, and the rest of the page keeps working.
 */
export function Floor3DPanel({
  warehouseId,
  floorId,
  published,
  highlightIds,
  highlightUnplaced = false,
  selectedId,
  onSelect,
  unavailableAction,
}: Floor3DPanelProps) {
  // A synchronous, one-time capability check — never re-run, since a browser does not gain or
  // lose WebGL mid-session. Checked before the scene is imported at all.
  const [webglSupported] = useState(hasWebGLSupport);
  useEffect(() => {
    if (!webglSupported) reportOnce("webgl_unavailable", "no_webgl");
  }, [webglSupported]);

  if (!webglSupported) {
    return <SceneUnavailable action={unavailableAction} />;
  }
  if (published?.published === true) {
    return (
      <PublishedScene
        published={published}
        highlightIds={highlightIds}
        selectedId={selectedId}
        onSelect={onSelect}
        unavailableAction={unavailableAction}
      />
    );
  }
  return (
    <AutoArrangedScene
      warehouseId={warehouseId}
      floorId={floorId}
      highlightIds={highlightIds}
      highlightUnplaced={highlightUnplaced}
      selectedId={selectedId}
      onSelect={onSelect}
      unavailableAction={unavailableAction}
    />
  );
}

interface PublishedSceneProps {
  published: PublishedLayoutOut;
  highlightIds: ReadonlySet<string>;
  selectedId: string | null;
  onSelect: (bin: BinSummary) => void;
  unavailableAction: ReactNode;
}

function PublishedScene({ published, highlightIds, selectedId, onSelect, unavailableAction }: PublishedSceneProps) {
  const layout = useMemo(
    () => placementsToSceneNodes(normalizePlacements(published.placements).drawable),
    [published],
  );

  if (layout.length === 0) {
    return (
      <Card className="p-6">
        <EmptyState
          icon={<BoxIcon className="size-6" />}
          title="Nothing on this floor's published layout can be drawn"
          description="Its locations have no usable size or position. They are listed under “Not on the floor plan”."
        />
      </Card>
    );
  }

  return (
    <SceneFrame
      layout={layout}
      source="the published floor plan"
      highlightIds={highlightIds}
      selectedId={selectedId}
      unavailableAction={unavailableAction}
      onSelectNode={(id) => {
        const node = layout.find((n) => n.location.id === id);
        if (node) {
          onSelect({
            id: node.location.id,
            code: node.location.code,
            name: node.location.name,
            kind: node.location.kind,
            details: null,
          });
        }
      }}
    />
  );
}

interface AutoArrangedSceneProps {
  warehouseId: string;
  floorId: string | null;
  highlightIds: ReadonlySet<string>;
  highlightUnplaced: boolean;
  selectedId: string | null;
  onSelect: (bin: BinSummary) => void;
  unavailableAction: ReactNode;
}

function AutoArrangedScene({
  warehouseId,
  floorId,
  highlightIds,
  highlightUnplaced,
  selectedId,
  onSelect,
  unavailableAction,
}: AutoArrangedSceneProps) {
  const locations = useAsync(() => listLocations(warehouseId), [warehouseId]);
  const slow = useSlowLoad(locations.status === "loading");
  const layout = useMemo(
    () => (locations.status === "success" ? computeWarehouseLayout(locations.data) : []),
    [locations],
  );

  return (
    <div className="flex flex-col gap-4">
      <InlineAlert
        tone="warning"
        title={
          floorId !== null
            ? "Layout is auto-arranged — this floor has no published floor plan yet"
            : highlightUnplaced
              ? "Layout is auto-arranged — this location isn't on the warehouse's published floor plan"
              : "Layout is auto-arranged — no floor plan has been configured for this warehouse yet"
        }
        live="off"
      >
        Positions and clusters below are computed from the location tree, not a surveyed floor plan. Colour marks each
        location&apos;s kind; size reflects its recorded weight or volume capacity where set.
      </InlineAlert>

      {locations.status === "loading" ? (
        <Card className="flex h-120 flex-col gap-4 p-6" aria-busy="true">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-full w-full rounded-tile" />
          <SlowLoadNotice show={slow} what="the locations" />
        </Card>
      ) : locations.status === "error" ? (
        <Card className="p-6">
          <ErrorState title="Couldn't load locations" message={problemMessage(locations.error)} onRetry={locations.reload} />
        </Card>
      ) : layout.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<BoxIcon className="size-6" />}
            title="No locations yet"
            description="Add a zone in the 2D view to start the tree — this view fills in once locations exist."
            action={unavailableAction}
          />
        </Card>
      ) : (
        <SceneFrame
          layout={layout}
          source="the location tree"
          highlightIds={highlightIds}
          selectedId={selectedId}
          unavailableAction={unavailableAction}
          onSelectNode={(id) => {
            const details = locations.status === "success" ? (locations.data.find((l) => l.id === id) ?? null) : null;
            if (details) onSelect({ id: details.id, code: details.code, name: details.name, kind: details.kind, details });
          }}
        />
      )}
    </div>
  );
}

interface SceneFrameProps {
  layout: WarehouseLayoutNode[];
  source: string;
  highlightIds: ReadonlySet<string>;
  selectedId: string | null;
  onSelectNode: (locationId: string) => void;
  unavailableAction: ReactNode;
}

function SceneFrame({ layout, source, highlightIds, selectedId, onSelectNode, unavailableAction }: SceneFrameProps) {
  const kindsPresent = useMemo(() => {
    const seen = new Set<LocationKind>();
    for (const node of layout) seen.add(node.location.kind);
    return Array.from(seen).sort((a, b) => LOCATION_KIND_LABEL[a].localeCompare(LOCATION_KIND_LABEL[b]));
  }, [layout]);
  const highlightedCount = useMemo(
    () => layout.filter((n) => highlightIds.has(n.location.id)).length,
    [layout, highlightIds],
  );

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      <Card className="h-120 flex-1 overflow-hidden p-0">
        <PanelBoundary
          title="The 3-D view"
          onFail={() => {
            reportOnce("floor_render_failed", "render_error");
            scene.reset();
          }}
          fallback={(retry) => <SceneFailed onRetry={retry} action={unavailableAction} />}
        >
          <Suspense fallback={<CanvasLoading />}>
            <Warehouse3DScene layout={layout} selectedId={selectedId} highlightIds={highlightIds} onSelect={onSelectNode} />
          </Suspense>
        </PanelBoundary>
      </Card>
      <SceneLegend kinds={kindsPresent} count={layout.length} source={source} highlightedCount={highlightedCount} />
    </div>
  );
}

function CanvasLoading() {
  const slow = useSlowLoad(true);
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-3" aria-busy="true">
      <Skeleton className="h-10 w-10" />
      <p className="text-sm text-muted">Loading the 3D scene…</p>
      <SlowLoadNotice show={slow} what="the 3-D scene" />
    </div>
  );
}

/** This browser has no WebGL, which a retry cannot change: it says so plainly and hands off to a
 *  view that can show the locations. */
function SceneUnavailable({ action }: { action: ReactNode }) {
  return (
    <Card className="p-6">
      <ErrorState
        tone="neutral"
        title="This browser can't show the 3D view"
        message="WebGL isn't available here — it may be disabled, or this device doesn't support it. The same locations are in the 2D / list view."
        action={action}
      />
    </Card>
  );
}

/** WebGL is there but the scene did not make it onto the screen: its code failed to download, or it
 *  threw while drawing. Both can pass, so this offers a retry as well as the view that does not
 *  need the scene. */
function SceneFailed({ onRetry, action }: { onRetry: () => void; action: ReactNode }) {
  return (
    <Card className="p-6">
      <ErrorState
        title="The 3D view couldn't be drawn"
        message="The 3-D scene failed to load or to draw. A dropped connection can cause this, as can this device's graphics. Try again; if it keeps happening, reload the page or use the 2D / list view."
        onRetry={onRetry}
        action={action}
      />
    </Card>
  );
}
