import { Component, Suspense, lazy, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getWarehouseById, listLocations } from "@/api/client";
import type { LocationKind, LocationOut } from "@/api/types";
import { LOCATION_KIND_LABEL } from "@/data/locationKinds";
import { LOCATION_KIND_COLOR } from "@/data/locationKindColor";
import { formatNumber } from "@/lib/format";
import { computeWarehouseLayout } from "@/lib/warehouseLayout";
import { hasWebGLSupport } from "@/lib/webgl";
import { problemMessage, useAsync } from "@opsgrids/client";
import { Button, Card, Drawer, EmptyState, ErrorState, InlineAlert, PageHeader, Skeleton } from "@/ui";
import { ArrowRightIcon, BoxIcon, cn } from "@opsgrids/ui";

/**
 * Every location, in one 3D view (Fulfilment F9 MVP) — `docs/fulfilment/plan.md` flags F9 as
 * depending on F7's floor-plan geometry, which does not exist yet (a `Location` has no x/y/z of
 * its own). Rather than wait, this ships against the one thing that is real and already live —
 * `listLocations` — with the layout computed client-side (`computeWarehouseLayout`) and the
 * screen honest about that everywhere: the persistent banner below, never a claim that this is
 * the warehouse's real floor plan.
 *
 * Sibling to `/warehouses/:id/locations` (the F1 tree screen), which links here and back —
 * neither hides the other. The tree screen stays the accessible, keyboard-first way to browse and
 * edit this same data; this view is a spatial read of it, pointer-driven like any WebGL scene.
 */
const Warehouse3DScene = lazy(() =>
  import("./Warehouse3DScene").then((m) => ({ default: m.Warehouse3DScene })),
);

export function Warehouse3DPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [selected, setSelected] = useState<LocationOut | null>(null);
  // A synchronous, one-time capability check — never re-run, since a browser does not gain or
  // lose WebGL mid-session. Checked before the scene (and `three`/`@react-three/fiber`/
  // `@react-three/drei`) is ever imported, so a browser without it never downloads that chunk.
  const [webglSupported] = useState(hasWebGLSupport);

  const warehouseLoader = () => getWarehouseById(id);
  const warehouseState = useAsync(warehouseLoader, [id]);
  const locationsLoader = () => listLocations(id);
  const locationsState = useAsync(locationsLoader, [id]);

  const layout = useMemo(
    () => (locationsState.status === "success" ? computeWarehouseLayout(locationsState.data) : []),
    [locationsState],
  );

  const kindsPresent = useMemo(() => {
    const seen = new Set<LocationKind>();
    for (const node of layout) seen.add(node.location.kind);
    return Array.from(seen).sort((a, b) => LOCATION_KIND_LABEL[a].localeCompare(LOCATION_KIND_LABEL[b]));
  }, [layout]);

  const warehouseName = warehouseState.status === "success" ? warehouseState.data.name : "Loading…";

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

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink warehouseId={id} />

      <PageHeader
        eyebrow="3D view"
        title={warehouseName}
        description="A spatial read of this warehouse's locations."
        actions={
          <Button variant="secondary" onClick={() => navigate(`/warehouses/${id}/locations`)}>
            2D / list view
          </Button>
        }
      />

      <InlineAlert tone="warning" title="Layout is auto-arranged — no floor plan has been configured for this warehouse yet" live="off">
        Positions and clusters below are computed from the location tree, not a surveyed floor plan. Colour marks each
        location's kind; size reflects its recorded weight or volume capacity where set.
      </InlineAlert>

      {locationsState.status === "loading" || warehouseState.status === "loading" ? (
        <Card className="flex h-120 flex-col gap-4 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-full w-full rounded-tile" />
        </Card>
      ) : locationsState.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            title="Couldn't load locations"
            message={problemMessage(locationsState.error)}
            onRetry={locationsState.reload}
          />
        </Card>
      ) : layout.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<BoxIcon className="size-6" />}
            title="No locations yet"
            description="Add a zone in the 2D view to start the tree — this view fills in once locations exist."
            action={
              <Button size="sm" onClick={() => navigate(`/warehouses/${id}/locations`)}>
                Go to 2D / list view
              </Button>
            }
          />
        </Card>
      ) : !webglSupported ? (
        <WebGLUnavailable warehouseId={id} />
      ) : (
        <div className="flex flex-col gap-4 lg:flex-row">
          <Card className="h-120 flex-1 overflow-hidden p-0">
            <Warehouse3DErrorBoundary warehouseId={id}>
              <Suspense fallback={<CanvasLoading />}>
                <Warehouse3DScene layout={layout} selectedId={selected?.id ?? null} onSelect={setSelected} />
              </Suspense>
            </Warehouse3DErrorBoundary>
          </Card>
          <Legend kinds={kindsPresent} count={layout.length} />
        </div>
      )}

      <Drawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={selected ? selected.name : "Location"}
        description={selected ? selected.code : undefined}
      >
        {selected ? <LocationDetail location={selected} /> : null}
      </Drawer>
    </div>
  );
}

function CanvasLoading() {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-3">
      <Skeleton className="h-10 w-10" />
      <p className="text-sm text-muted">Loading the 3D scene…</p>
    </div>
  );
}

function Legend({ kinds, count }: { kinds: LocationKind[]; count: number }) {
  return (
    <Card className="flex w-full flex-col gap-3 p-4 lg:w-64">
      <div className="flex flex-col gap-1">
        <p className="text-sm font-bold text-ink">Legend</p>
        <p className="text-xs text-muted">
          {formatNumber(count)} location{count === 1 ? "" : "s"} shown, coloured by kind.
        </p>
      </div>
      <ul className="flex flex-col gap-2">
        {kinds.map((kind) => (
          <li key={kind} className="flex items-center gap-2 text-sm">
            <span className={cn("size-3 shrink-0 rounded-chip", LOCATION_KIND_COLOR[kind].swatchClass)} aria-hidden />
            <span className="text-ink-soft">{LOCATION_KIND_LABEL[kind]}</span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-faint">Click a box to see that location's recorded fields.</p>
    </Card>
  );
}

function FieldRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-t border-line py-3 text-sm first:border-t-0 first:pt-0">
      <dt className="text-muted">{label}</dt>
      <dd className="tnum text-right font-semibold text-ink">{value}</dd>
    </div>
  );
}

function LocationDetail({ location }: { location: LocationOut }) {
  return (
    <div className="flex flex-col gap-4">
      <dl className="flex flex-col">
        <FieldRow label="Kind" value={LOCATION_KIND_LABEL[location.kind]} />
        <FieldRow label="Code" value={location.code} />
        <FieldRow label="Barcode" value={location.barcode ?? "—"} />
        <FieldRow label="Pick sequence" value={location.pick_sequence !== null ? formatNumber(location.pick_sequence) : "—"} />
        <FieldRow
          label="Max weight"
          value={location.max_weight_grams !== null ? `${formatNumber(location.max_weight_grams)} g` : "—"}
        />
        <FieldRow
          label="Max volume"
          value={location.max_volume_milli !== null ? `${formatNumber(location.max_volume_milli)} mL` : "—"}
        />
        <FieldRow label="Storage type" value={location.storage_type ?? "—"} />
        <FieldRow label="Default location" value={location.is_default ? "Yes" : "No"} />
      </dl>
      <InlineAlert tone="info" title="Stock detail not available yet" live="off">
        This location's data has no stock level or SKU field, and no live inventory-by-location endpoint exists yet.
      </InlineAlert>
    </div>
  );
}

/** The one fallback both the no-WebGL and the render-error paths share: the 3D view genuinely
 *  cannot show anything here, so it says so plainly and hands off to the real, existing 2D/list
 *  view rather than a blank canvas or a crashed page. */
function WebGLUnavailable({ warehouseId }: { warehouseId: string }) {
  const navigate = useNavigate();
  return (
    <Card className="p-6">
      <ErrorState
        tone="neutral"
        title="This browser can't show the 3D view"
        message="WebGL isn't available here — it may be disabled, or this device doesn't support it. The same locations are in the 2D / list view."
        action={
          <Button variant="secondary" size="sm" onClick={() => navigate(`/warehouses/${warehouseId}/locations`)}>
            Go to 2D / list view
          </Button>
        }
      />
    </Card>
  );
}

interface BoundaryProps {
  warehouseId: string;
  children: ReactNode;
}
interface BoundaryState {
  failed: boolean;
}

/** Catches a runtime failure in the 3D scene (a `three`/WebGL error the feature-detect above
 *  could not predict) and falls back to the same message the no-WebGL path shows, rather than
 *  taking the whole page down with it. React itself still reports the error to the console
 *  (its own default error-boundary behaviour) — nothing here needs to duplicate that. */
class Warehouse3DErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  override state: BoundaryState = { failed: false };

  static getDerivedStateFromError(): BoundaryState {
    return { failed: true };
  }

  override render(): ReactNode {
    if (this.state.failed) return <WebGLUnavailable warehouseId={this.props.warehouseId} />;
    return this.props.children;
  }
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
