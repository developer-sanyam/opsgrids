import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { getWarehouseById } from "@/api/client";
import type { PublishedLayoutOut } from "@/api/types";
import { BinDrawer } from "@/floor/BinDrawer";
import type { BinSummary } from "@/floor/binSummary";
import { Floor3DPanel } from "@/floor/Floor3DPanel";
import { FloorPlan2D } from "@/floor/FloorPlan2D";
import { FloorSelect } from "@/floor/FloorSelect";
import { SkippedPlacements } from "@/floor/SkippedPlacements";
import { usePublishedLayout } from "@/floor/usePublishedLayout";
import { reportOnce } from "@/lib/clientReport";
import { MAX_HIGHLIGHT_IDS, normalizePlacements, parseHighlight, summarizeHighlight } from "@/lib/floorGeometry";
import { Can, problemMessage, RequirePermission, useAsync } from "@opsgrids/client";
import { Button, Card, EmptyState, ErrorState, PageHeader, Skeleton, Tabs } from "@/ui";
import { PanelBoundary } from "@/ui/PanelBoundary";
import { useSlowLoad } from "@/lib/useSlowLoad";
import { SlowLoadNotice } from "@/ui/SlowLoadNotice";
import { ArrowRightIcon, LayersIcon } from "@opsgrids/ui";
import { NoAccess } from "@/tracking/pageParts";

type View = "2d" | "3d";

/**
 * One floor, as it is published: a flat 2-D plan (the default, no WebGL needed) and a 3-D view, both
 * drawn from the same `GET /operations/floors/{floor_id}/published-layout` response. Only published geometry ever
 * appears — a draft someone is still editing is not on this screen.
 *
 * This is where "Show on floor" from Find stock lands: `?highlight=` carries the location ids a
 * search found, and both views mark them. The marking is outline, pin and label as well as colour,
 * and a status line says in words what was found and what was not.
 */
export function FloorViewPage() {
  return (
    <RequirePermission
      perm="operations.warehouse.layout.read"
      scope="ORG"
      fallback={<NoAccess what="floor plans" />}
    >
      <FloorViewScreen />
    </RequirePermission>
  );
}

function FloorViewScreen() {
  const { id: warehouseId = "", floorId = "" } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const view: View = params.get("view") === "3d" ? "3d" : "2d";
  const { ids: highlightIds, total: highlightTotal } = useMemo(() => parseHighlight(params.get("highlight")), [params]);
  const highlightSet = useMemo(() => new Set(highlightIds), [highlightIds]);

  const published = usePublishedLayout(floorId);
  const warehouse = useAsync(() => getWarehouseById(warehouseId), [warehouseId]);
  const slow = useSlowLoad(published.status === "loading");
  const [bin, setBin] = useState<BinSummary | null>(null);

  const layout: PublishedLayoutOut | null = published.status === "success" ? published.data : null;
  const normalized = useMemo(() => normalizePlacements(layout?.placements ?? []), [layout]);

  function setView(next: string) {
    setParams(
      (current) => {
        const copy = new URLSearchParams(current);
        if (next === "3d") copy.set("view", "3d");
        else copy.delete("view");
        return copy;
      },
      { replace: true },
    );
  }

  function changeFloor(nextFloorId: string) {
    if (nextFloorId === "" || nextFloorId === floorId) return;
    navigate(`/warehouses/${warehouseId}/floors/${nextFloorId}/view${view === "3d" ? "?view=3d" : ""}`);
  }

  const floorName = layout?.floor_name ?? "Floor";
  const warehouseName = warehouse.status === "success" ? warehouse.data.name : "Floor plan";
  const to2D = (
    <Button variant="secondary" size="sm" onClick={() => setView("2d")}>
      Show the 2-D plan
    </Button>
  );

  const highlightStatus = (() => {
    if (highlightIds.length === 0 || layout === null) return "";
    // A link can carry more ids than a view marks. Say so, or "Highlighting 100" reads as everything.
    const cap =
      highlightTotal > MAX_HIGHLIGHT_IDS
        ? ` Only the first ${MAX_HIGHLIGHT_IDS} of the ${highlightTotal} locations in this link are marked.`
        : "";
    if (!layout.published) {
      return `${highlightIds.length} location${highlightIds.length === 1 ? "" : "s"} to highlight. This floor has no published plan, so they are marked in the auto-arranged 3-D view.${cap}`;
    }
    const { found, missing } = summarizeHighlight(normalized.drawable, highlightIds);
    const foundText =
      found.length > 0 ? `Highlighting ${found.length} location${found.length === 1 ? "" : "s"} on this floor: ${found.join(", ")}.` : "";
    const missingText =
      missing > 0 ? ` ${missing} of the locations you searched for ${missing === 1 ? "is" : "are"} not on this floor's plan.` : "";
    return `${foundText}${missingText}${cap}`.trim();
  })();

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to={`/warehouses/${warehouseId}/floors`}
        className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
      >
        <ArrowRightIcon className="size-4 rotate-180" />
        Back to floors
      </Link>

      <PageHeader
        eyebrow={warehouseName}
        title={floorName}
        description="The published floor plan. Select a location to see what is stored in it."
        actions={
          <>
            <FloorSelect warehouseId={warehouseId} value={floorId} onChange={changeFloor} />
            <Can permission="operations.warehouse.layout.write" scope="ORG">
              <Button variant="secondary" onClick={() => navigate(`/warehouses/${warehouseId}/floors/${floorId}`)}>
                Edit layout
              </Button>
            </Can>
          </>
        }
      />

      <p className="text-sm text-ink" role="status" aria-live="polite">
        {highlightStatus}
      </p>

      {published.status === "loading" ? (
        <Card className="flex flex-col gap-3 p-6" aria-busy="true">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-96 w-full rounded-tile" />
          <SlowLoadNotice show={slow} what="the floor plan" />
        </Card>
      ) : null}

      {published.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            title="Couldn't load this floor's plan"
            message={problemMessage(published.error)}
            onRetry={published.reload}
            action={
              <Button variant="secondary" size="sm" onClick={() => navigate(`/warehouses/${warehouseId}/floors`)}>
                Back to floors
              </Button>
            }
          />
        </Card>
      ) : null}

      {layout !== null ? (
        <>
          <Tabs
            label="Floor views"
            variant="segmented"
            activation="manual"
            items={[
              { id: "2d", label: "2-D plan" },
              { id: "3d", label: "3-D view" },
            ]}
            value={view}
            onChange={setView}
          >
            {view === "2d" ? (
              <PlanPanel
                layout={layout}
                drawable={normalized.drawable}
                highlightIds={highlightSet}
                selectedId={bin?.id ?? null}
                onSelect={setBin}
                onShow3D={() => setView("3d")}
              />
            ) : (
              <Floor3DPanel
                warehouseId={warehouseId}
                floorId={floorId}
                published={layout}
                highlightIds={highlightSet}
                selectedId={bin?.id ?? null}
                onSelect={setBin}
                unavailableAction={to2D}
              />
            )}
          </Tabs>
          <SkippedPlacements placements={normalized.skipped} />
        </>
      ) : null}

      <BinDrawer bin={bin} onClose={() => setBin(null)} />
    </div>
  );
}

interface PlanPanelProps {
  layout: PublishedLayoutOut;
  drawable: ReturnType<typeof normalizePlacements>["drawable"];
  highlightIds: ReadonlySet<string>;
  selectedId: string | null;
  onSelect: (bin: BinSummary) => void;
  onShow3D: () => void;
}

function PlanPanel({ layout, drawable, highlightIds, selectedId, onSelect, onShow3D }: PlanPanelProps) {
  if (!layout.published) {
    return (
      <Card className="p-6">
        <EmptyState
          icon={<LayersIcon className="size-6" />}
          title="This floor has no published layout yet"
          description="Publish a layout in the editor to see the real plan here. The 3-D view shows an auto-arranged picture of the locations in the meantime."
          action={
            <Button size="sm" variant="secondary" onClick={onShow3D}>
              Open the 3-D view
            </Button>
          }
        />
      </Card>
    );
  }
  if (drawable.length === 0) {
    return (
      <Card className="p-6">
        <EmptyState
          icon={<LayersIcon className="size-6" />}
          title="Nothing on this floor's published layout can be drawn"
          description="Its locations have no usable size or position. They are listed under “Not on the floor plan”."
        />
      </Card>
    );
  }
  return (
    <Card className="p-2 sm:p-4">
      <PanelBoundary title="The floor plan" onFail={() => reportOnce("floor_render_failed", "render_error")}>
        <FloorPlan2D
          rects={drawable}
          highlightIds={highlightIds}
          selectedId={selectedId}
          onSelect={(id) => {
            const rect = drawable.find((r) => r.locationId === id);
            if (rect) onSelect({ id: rect.locationId, code: rect.code, name: rect.name, kind: rect.kind, details: null });
          }}
          label={`Floor plan of ${layout.floor_name}`}
        />
      </PanelBoundary>
    </Card>
  );
}
