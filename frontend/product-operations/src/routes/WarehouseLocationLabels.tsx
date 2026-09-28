import { useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { getWarehouseById, listLocations } from "@/api/client";
import type { LocationOut } from "@/api/types";
import { LOCATION_KIND_LABEL } from "@/data/locationKinds";
import { flattenTree, zoneSubtree } from "@/lib/locationTree";
import { problemMessage, useAsync } from "@opsgrids/client";
import { Button, Card, ErrorState, QrCode, Skeleton } from "@/ui";
import { ArrowRightIcon } from "@opsgrids/ui";

/**
 * A printable label sheet for one zone — `docs/fulfilment/plan.md` F1's "print labels" action.
 * No server PDF endpoint exists for F1 (`services/document` doesn't support PDFs yet); this is
 * entirely client-rendered, from the same location list the tree screen already fetches, and
 * printed with `window.print()`.
 *
 * Deliberately its own route with no `AppShell` chrome around it (see `main.tsx`) — a sidebar
 * and top bar have no reason to occupy a warehouse worker's printed page, and fighting them with
 * print stylesheets buys nothing a plain, unshelled route doesn't already give for free. The
 * "Back" link and the print button are hidden at print time (`print:hidden`); everything else on
 * the page is the sheet itself.
 */
export function WarehouseLocationLabelsPage() {
  const { id = "" } = useParams();
  const [searchParams] = useSearchParams();
  const zoneId = searchParams.get("zone") ?? "";

  const warehouseLoader = () => getWarehouseById(id);
  const warehouseState = useAsync(warehouseLoader, [id]);
  const locationsLoader = () => listLocations(id);
  const locationsState = useAsync(locationsLoader, [id]);

  const labels = useMemo(() => {
    if (locationsState.status !== "success" || zoneId === "") return [];
    return zoneSubtree(flattenTree(locationsState.data), zoneId);
  }, [locationsState, zoneId]);

  const zone: LocationOut | undefined = labels[0];

  return (
    <div className="min-h-dvh bg-ground">
      <div className="print:hidden flex flex-wrap items-center justify-between gap-4 border-b border-line bg-surface px-6 py-4">
        <BackLink warehouseId={id} />
        <Button onClick={() => window.print()} disabled={labels.length === 0}>
          Print
        </Button>
      </div>

      <div className="mx-auto flex max-w-3xl flex-col gap-6 p-6 print:max-w-none print:p-0">
        {zoneId === "" ? (
          <ErrorState title="No zone specified" message="This link is missing which zone to print labels for." />
        ) : warehouseState.status === "error" ? (
          <ErrorState
            title="Couldn't load this warehouse"
            message={problemMessage(warehouseState.error)}
            onRetry={warehouseState.reload}
          />
        ) : locationsState.status === "loading" || warehouseState.status === "loading" ? (
          <div className="flex flex-col gap-4 print:hidden">
            <Skeleton className="h-8 w-64" />
            <div className="grid grid-cols-2 gap-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-48 w-full rounded-tile" />
              ))}
            </div>
          </div>
        ) : locationsState.status === "error" ? (
          <ErrorState
            title="Couldn't load locations"
            message={problemMessage(locationsState.error)}
            onRetry={locationsState.reload}
          />
        ) : !zone ? (
          <ErrorState title="That zone could not be found" message="It may have been removed. Go back and pick another one." />
        ) : (
          <>
            <div className="flex flex-col gap-1 print:hidden">
              <h1 className="text-page-sm">
                Labels — {zone.name}
                {warehouseState.status === "success" ? ` (${warehouseState.data.name})` : ""}
              </h1>
              <p className="text-sm text-muted">
                {labels.length} label{labels.length === 1 ? "" : "s"}, one per location in this zone.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 print:grid-cols-2">
              {labels.map((location) => (
                <LabelCard key={location.id} location={location} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function LabelCard({ location }: { location: LocationOut }) {
  return (
    <Card className="flex flex-col items-center gap-3 break-inside-avoid p-4 text-center">
      <span className="text-xs font-semibold uppercase tracking-wide text-muted">
        {LOCATION_KIND_LABEL[location.kind]}
      </span>
      <span className="text-lg font-extrabold text-ink">{location.code}</span>
      <span className="text-sm text-ink-soft">{location.name}</span>
      {location.barcode ? (
        <>
          <QrCode value={location.barcode} label={`Barcode for ${location.name}`} size={140} />
          <span className="tnum text-xs font-semibold text-ink">{location.barcode}</span>
        </>
      ) : (
        <span className="text-xs text-faint">No barcode set</span>
      )}
    </Card>
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
