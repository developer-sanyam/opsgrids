import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { getWarehouseById } from "@/api/client";
import { BinDrawer } from "@/floor/BinDrawer";
import type { BinSummary } from "@/floor/binSummary";
import { Floor3DPanel } from "@/floor/Floor3DPanel";
import { FloorSelect } from "@/floor/FloorSelect";
import { SkippedPlacements } from "@/floor/SkippedPlacements";
import { usePublishedLayout } from "@/floor/usePublishedLayout";
import { normalizePlacements, parseHighlightIds } from "@/lib/floorGeometry";
import { problemMessage, useAsync } from "@opsgrids/client";
import { Button, Card, ErrorState, PageHeader, Skeleton } from "@/ui";
import { useSlowLoad } from "@/lib/useSlowLoad";
import { SlowLoadNotice } from "@/ui/SlowLoadNotice";
import { ArrowRightIcon } from "@opsgrids/ui";

/**
 * Every location of a warehouse in one 3D view (Fulfilment F9), now with a floor selector.
 *
 * Choose a floor that has a published layout and the view is drawn from that layout's real
 * placements, with nothing on the page calling it approximate. Choose no floor, or a floor with no
 * published layout, and it is the auto-arranged picture computed from the location tree, under the
 * banner that says so (`Floor3DPanel`). `?floor=` picks the floor and `?highlight=` marks locations
 * a search found, the same parameter the floor view reads.
 *
 * Sibling to `/warehouses/:id/locations` (the F1 tree screen), which links here and back — neither
 * hides the other. The tree screen stays the accessible, keyboard-first way to browse and edit this
 * same data; this view is a spatial read of it, pointer-driven like any WebGL scene.
 */
export function Warehouse3DPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const floorId = params.get("floor") ?? "";
  const highlightSet = useMemo(() => new Set(parseHighlightIds(params.get("highlight"))), [params]);
  const [bin, setBin] = useState<BinSummary | null>(null);

  const warehouseState = useAsync(() => getWarehouseById(id), [id]);
  const published = usePublishedLayout(floorId === "" ? null : floorId);
  const slow = useSlowLoad(published.status === "loading" && floorId !== "");
  const skipped = useMemo(
    () => (published.status === "success" && published.data ? normalizePlacements(published.data.placements).skipped : []),
    [published],
  );

  const warehouseName = warehouseState.status === "success" ? warehouseState.data.name : "Loading…";

  function changeFloor(next: string) {
    setParams(
      (current) => {
        const copy = new URLSearchParams(current);
        if (next === "") copy.delete("floor");
        else copy.set("floor", next);
        return copy;
      },
      { replace: true },
    );
    setBin(null);
  }

  if (warehouseState.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink warehouseId={id} />
        <Card className="p-6">
          <ErrorState
            title="Couldn't load this warehouse"
            message={problemMessage(warehouseState.error)}
            onRetry={warehouseState.reload}
          />
        </Card>
      </div>
    );
  }

  const toList = (
    <Button variant="secondary" size="sm" onClick={() => navigate(`/warehouses/${id}/locations`)}>
      Go to 2D / list view
    </Button>
  );

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink warehouseId={id} />

      <PageHeader
        eyebrow="3D view"
        title={warehouseName}
        description="A spatial read of this warehouse's locations."
        actions={
          <>
            <FloorSelect warehouseId={id} value={floorId} onChange={changeFloor} noneLabel="Auto-arranged (all locations)" />
            <Button variant="secondary" onClick={() => navigate(`/warehouses/${id}/locations`)}>
              2D / list view
            </Button>
          </>
        }
      />

      {warehouseState.status === "loading" || (floorId !== "" && published.status === "loading") ? (
        <Card className="flex h-120 flex-col gap-4 p-6" aria-busy="true">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-full w-full rounded-tile" />
          <SlowLoadNotice show={slow} what="the floor" />
        </Card>
      ) : published.status === "error" ? (
        <Card className="p-6">
          <ErrorState title="Couldn't load this floor's layout" message={problemMessage(published.error)} onRetry={published.reload} />
        </Card>
      ) : (
        <>
          <Floor3DPanel
            warehouseId={id}
            floorId={floorId === "" ? null : floorId}
            published={published.status === "success" ? published.data : null}
            highlightIds={highlightSet}
            selectedId={bin?.id ?? null}
            onSelect={setBin}
            unavailableAction={toList}
          />
          <SkippedPlacements placements={skipped} />
        </>
      )}

      <BinDrawer bin={bin} onClose={() => setBin(null)} />
    </div>
  );
}

function BackLink({ warehouseId }: { warehouseId: string }) {
  return (
    <Link
      to={`/warehouses/${warehouseId}/locations`}
      className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to locations
    </Link>
  );
}
