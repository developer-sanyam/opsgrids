import { Suspense, lazy, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { getWarehousesMap } from "@/api/client";
import type { WarehouseMapSiteOut, WarehouseOut, WarehouseUnlocatedOut } from "@/api/types";
import { WarehouseFormDialog } from "@/forms/WarehouseFormDialog";
import { reportOnce } from "@/lib/clientReport";
import type { ReportReason } from "@/lib/clientReport";
import { formatNumber } from "@/lib/format";
import { isValidMicroPair } from "@/lib/geo";
import { getMapsApiKey } from "@/lib/mapsConfig";
import { problemMessage, useAsync, useCan } from "@opsgrids/client";
import { Button, Card, DataTable, EmptyState, ErrorState, InlineAlert, PageHeader, RowActions, SectionHeader, Skeleton, Tag } from "@/ui";
import type { Column } from "@/ui";
import { PanelBoundary } from "@/ui/PanelBoundary";
import { useSlowLoad } from "@/lib/useSlowLoad";
import { SlowLoadNotice } from "@/ui/SlowLoadNotice";
import { MapPinIcon, useToast, WarehouseIcon } from "@opsgrids/ui";

const SitesMap = lazy(() => import("@/maps/SitesMap"));

type MapFailure = Extract<ReportReason, "script_error" | "auth_failure">;

/**
 * Every warehouse the organization has, on a map where it has a position and in a list either way.
 *
 * The map is an enhancement, never a dependency. A build without `VITE_GOOGLE_MAPS_API_KEY`, a
 * script that does not load, a key Google refuses, a chunk that fails to download or a render
 * error inside the map all end in the same place: the list, plus a sentence saying why there is no
 * map. The list carries everything the popup does and the same actions, so nothing is reachable
 * only through the map. Warehouses with no position are listed apart, each with "Set location".
 */
export function WarehousesMapPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [params] = useSearchParams();
  const canWrite = useCan("operations.inventory.warehouse.write", "ORG");
  const state = useAsync(() => getWarehousesMap(), []);
  const slow = useSlowLoad(state.status === "loading");

  const apiKey = getMapsApiKey();
  const [failure, setFailure] = useState<MapFailure | null>(null);
  const [mapAttempt, setMapAttempt] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(params.get("focus"));
  const [locating, setLocating] = useState<WarehouseOut | null>(null);

  function mapFailed(reason: MapFailure) {
    reportOnce("maps_load_failed", reason);
    setFailure(reason);
  }

  const sites = state.status === "success" ? state.data.sites : [];
  const placed = sites.filter((s) => isValidMicroPair(s.lat_micro, s.lng_micro));
  const unlocated: WarehouseUnlocatedOut[] =
    state.status === "success"
      ? [
          ...state.data.without_coordinates,
          ...sites
            .filter((s) => !isValidMicroPair(s.lat_micro, s.lng_micro))
            .map(({ id, code, name, location }) => ({ id, code, name, location })),
        ]
      : [];
  const mapVisible = apiKey !== null && failure === null && placed.length > 0;

  const siteColumns: Column<WarehouseMapSiteOut>[] = [
    {
      key: "name",
      header: "Warehouse",
      mobile: "title",
      render: (s) => (
        <div className="flex flex-wrap items-center gap-2">
          <span className="tnum text-xs font-semibold text-muted">{s.code}</span>
          <span className="font-semibold text-ink">{s.name}</span>
          {s.id === selectedId ? <Tag tone="info">Selected</Tag> : null}
        </div>
      ),
    },
    {
      key: "location",
      header: "Address",
      render: (s) => <span className="text-ink-soft">{s.location || "—"}</span>,
    },
    {
      key: "floors",
      header: "Floors",
      align: "right",
      render: (s) => <span className="tnum text-ink-soft">{formatNumber(s.floor_count)}</span>,
    },
    {
      key: "stocked",
      header: "Stocked bins",
      align: "right",
      render: (s) => <span className="tnum text-ink-soft">{formatNumber(s.stocked_location_count)}</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (s) => (
        <RowActions
          label={s.name}
          actions={[
            { label: "Open floors", onSelect: () => navigate(`/warehouses/${s.id}/floors`) },
            { label: "Show on map", onSelect: () => setSelectedId(s.id), hidden: !mapVisible },
            { label: "Select", onSelect: () => setSelectedId(s.id), hidden: mapVisible },
          ]}
        />
      ),
    },
  ];

  const unlocatedColumns: Column<WarehouseUnlocatedOut>[] = [
    {
      key: "name",
      header: "Warehouse",
      mobile: "title",
      render: (w) => (
        <div className="flex flex-wrap items-center gap-2">
          <span className="tnum text-xs font-semibold text-muted">{w.code}</span>
          <span className="font-semibold text-ink">{w.name}</span>
        </div>
      ),
    },
    {
      key: "location",
      header: "Address",
      render: (w) => <span className="text-ink-soft">{w.location || "—"}</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (w) =>
        canWrite ? (
          <Button size="sm" variant="secondary" onClick={() => setLocating({ id: w.id, code: w.code, name: w.name, location: w.location })}>
            Set location
          </Button>
        ) : (
          <span className="text-xs text-muted">Ask an administrator</span>
        ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Inventory"
        title="Warehouse map"
        description="Where each warehouse is, and the way into its floors."
        actions={
          <Button variant="secondary" onClick={() => navigate("/warehouses")}>
            Warehouse list
          </Button>
        }
      />

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-3 p-4 sm:p-6" aria-busy="true">
          <Skeleton className="h-96 w-full rounded-tile" />
          <SlowLoadNotice show={slow} what="the warehouses" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState title="Couldn't load the warehouse map" message={problemMessage(state.error)} onRetry={state.reload} />
        </Card>
      ) : null}

      {state.status === "success" && sites.length === 0 && unlocated.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<WarehouseIcon className="size-6" />}
            title="No warehouses yet"
            description="Add a warehouse first; once it has a location it appears here."
            action={
              <Button size="sm" onClick={() => navigate("/warehouses")}>
                Go to warehouses
              </Button>
            }
          />
        </Card>
      ) : null}

      {state.status === "success" && (sites.length > 0 || unlocated.length > 0) ? (
        <>
          {apiKey === null ? (
            <InlineAlert tone="info" title="The map isn't set up here, so your warehouses are listed instead" live="off">
              Everything on this page still works from the list.
            </InlineAlert>
          ) : null}
          {apiKey !== null && failure !== null ? (
            <InlineAlert
              tone="warning"
              title="The map couldn't load, so your warehouses are listed instead"
              action={
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    setFailure(null);
                    setMapAttempt((n) => n + 1);
                  }}
                >
                  Try the map again
                </Button>
              }
            >
              Everything on this page still works from the list.
            </InlineAlert>
          ) : null}
          {apiKey !== null && failure === null && placed.length === 0 ? (
            <InlineAlert tone="info" title="No warehouse has a location yet" live="off">
              Set one below and it appears on the map.
            </InlineAlert>
          ) : null}

          {mapVisible && apiKey !== null ? (
            <Card className="flex flex-col gap-3 p-2 sm:p-4">
              <section aria-label="Map of warehouses">
                <PanelBoundary
                  key={mapAttempt}
                  title="The map"
                  onFail={() => mapFailed("script_error")}
                  fallback={() => null}
                >
                  <Suspense fallback={<MapLoading />}>
                    <SitesMap
                      apiKey={apiKey}
                      sites={placed}
                      selectedId={selectedId}
                      onSelect={setSelectedId}
                      onOpenFloors={(id) => navigate(`/warehouses/${id}/floors`)}
                      onFailure={mapFailed}
                    />
                  </Suspense>
                </PanelBoundary>
              </section>
              <p className="px-2 text-xs text-muted">
                The list below carries the same information and actions; the map is a visual aid.
              </p>
            </Card>
          ) : null}

          {placed.length > 0 ? (
            <Card className="p-2 sm:p-4">
              <div className="px-3 pt-2 pb-3">
                <SectionHeader title="Warehouses" subtitle="Every warehouse with a location." />
              </div>
              <p className="sr-only" role="status" aria-live="polite">
                {selectedId !== null && placed.some((s) => s.id === selectedId)
                  ? `Selected ${placed.find((s) => s.id === selectedId)?.name ?? ""}`
                  : ""}
              </p>
              <DataTable
                columns={siteColumns}
                rows={placed}
                rowKey={(s) => s.id}
                caption="Warehouses with a location"
                mobileCard="auto"
              />
            </Card>
          ) : null}

          {unlocated.length > 0 ? (
            <Card className="p-2 sm:p-4">
              <div className="px-3 pt-2 pb-3">
                <SectionHeader
                  title="Without a location"
                  subtitle="These can't be shown on the map until someone sets where they are."
                />
              </div>
              <DataTable
                columns={unlocatedColumns}
                rows={unlocated}
                rowKey={(w) => w.id}
                caption="Warehouses without a location"
                mobileCard="auto"
              />
            </Card>
          ) : null}
        </>
      ) : null}

      {locating ? (
        <WarehouseFormDialog
          warehouse={locating}
          onClose={() => setLocating(null)}
          onSaved={(warehouse) => {
            setLocating(null);
            toast.success(`${warehouse.name} saved.`);
            state.reload();
          }}
        />
      ) : null}
    </div>
  );
}

function MapLoading() {
  const slow = useSlowLoad(true);
  return (
    <div className="flex flex-col gap-3" aria-busy="true">
      <div className="flex h-96 w-full flex-col items-center justify-center gap-3 rounded-tile bg-sunken">
        <MapPinIcon className="size-6 text-muted" aria-hidden />
        <p className="text-sm text-muted">Loading the map…</p>
      </div>
      <SlowLoadNotice show={slow} what="the map" />
    </div>
  );
}
