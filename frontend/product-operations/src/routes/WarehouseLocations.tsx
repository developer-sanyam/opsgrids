import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getWarehouseById, listLocations } from "@/api/client";
import type { LocationOut } from "@/api/types";
import { LOCATION_KIND_LABEL } from "@/data/locationKinds";
import { flattenTree, indentClass } from "@/lib/locationTree";
import type { LocationRow } from "@/lib/locationTree";
import { Can, problemMessage, useAsync, useCan } from "@opsgrids/client";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  RowActions,
  Skeleton,
  Tag,
} from "@/ui";
import type { Column, SelectOption } from "@/ui";
import { ArrowRightIcon, MapPinIcon, PlusIcon, useToast } from "@opsgrids/ui";
import { LocationFormDialog } from "@/forms/LocationFormDialog";

/** Every location in the warehouse as "Parent location" options for the create dialog — indented
 *  the same way the tree itself is, so the select reads as the tree it is choosing a spot in. */
function toParentOptions(rows: LocationRow[]): SelectOption[] {
  return rows.map((r) => ({
    value: r.location.id,
    label: `${"  ".repeat(r.depth)}${LOCATION_KIND_LABEL[r.location.kind]} · ${r.location.code} — ${r.location.name}`,
  }));
}

/**
 * A warehouse's location tree (Fulfilment plan F1) — zone → aisle/rack/shelf/bin and the
 * functional top-level kinds (staging, dock, receiving, …), depth-first and indented rather than
 * a drag-drop editor (deferred past F1). `operations.inventory.location.write` gates every write;
 * `operations.inventory.layout.publish` additionally gates kind/capacity changes inside the edit
 * dialog itself.
 */
export function WarehouseLocationsPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const canWrite = useCan("operations.inventory.location.write", "ORG");
  const [creatingUnderParentId, setCreatingUnderParentId] = useState<string | null | undefined>(undefined);
  const [editing, setEditing] = useState<LocationOut | null>(null);

  const warehouseLoader = () => getWarehouseById(id);
  const warehouseState = useAsync(warehouseLoader, [id]);
  const locationsLoader = () => listLocations(id);
  const locationsState = useAsync(locationsLoader, [id]);

  const rows = useMemo(
    () => (locationsState.status === "success" ? flattenTree(locationsState.data) : []),
    [locationsState],
  );
  const parentOptions = useMemo(() => toParentOptions(rows), [rows]);

  const columns: Column<LocationRow>[] = [
    {
      key: "location",
      header: "Location",
      mobile: "title",
      render: (r) => (
        <div className={`${indentClass(r.depth)} flex flex-col`}>
          <span className="tnum text-xs font-semibold text-muted">{r.location.code}</span>
          <span className="font-semibold text-ink">{r.location.name}</span>
        </div>
      ),
    },
    {
      key: "kind",
      header: "Kind",
      mobile: "status",
      render: (r) => <Tag>{LOCATION_KIND_LABEL[r.location.kind]}</Tag>,
    },
    {
      key: "barcode",
      header: "Barcode",
      mobile: "field",
      render: (r) =>
        r.location.barcode ? (
          <span className="tnum text-ink-soft">{r.location.barcode}</span>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "pick_sequence",
      header: "Pick seq.",
      align: "right",
      mobile: "field",
      render: (r) => (
        <span className="tnum text-ink-soft">
          {r.location.pick_sequence !== null ? r.location.pick_sequence : "—"}
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (r) => (
        <RowActions
          label={r.location.name}
          actions={[
            { label: "Edit", onSelect: () => setEditing(r.location), hidden: !canWrite },
            {
              label: "Add child",
              onSelect: () => setCreatingUnderParentId(r.location.id),
              hidden: !canWrite,
            },
            {
              label: "Print labels",
              onSelect: () => navigate(`/warehouses/${id}/locations/labels?zone=${r.location.id}`),
              hidden: r.location.kind !== "zone",
            },
          ]}
        />
      ),
    },
  ];

  if (warehouseState.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink />
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
      <BackLink />

      <PageHeader
        eyebrow="Locations"
        title={warehouseState.status === "success" ? warehouseState.data.name : "Loading…"}
        description="The zone → aisle/rack → shelf/bin tree stock is filed under, with each location's barcode and pick sequence."
        actions={
          <>
            <Button variant="secondary" onClick={() => navigate(`/warehouses/${id}/3d`)}>
              3D view
            </Button>
            <Can permission="operations.inventory.location.write" scope="ORG">
              <Button
                leadingIcon={<PlusIcon className="size-4" />}
                onClick={() => setCreatingUnderParentId(null)}
                disabled={warehouseState.status !== "success"}
              >
                New location
              </Button>
            </Can>
          </>
        }
      />

      <Card className="p-2 sm:p-4">
        {locationsState.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {locationsState.status === "error" ? (
          <ErrorState
            title="Couldn't load locations"
            message={problemMessage(locationsState.error)}
            onRetry={locationsState.reload}
          />
        ) : null}
        {locationsState.status === "success" ? (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(r) => r.location.id}
            caption={warehouseState.status === "success" ? `Locations in ${warehouseState.data.name}` : "Locations"}
            mobileCard="auto"
            emptyState={
              <EmptyState
                icon={<MapPinIcon className="size-6" />}
                title="No locations yet"
                description="Add a zone to start the tree — aisles, racks, shelves and bins nest under it."
                action={
                  <Can permission="operations.inventory.location.write" scope="ORG">
                    <Button size="sm" onClick={() => setCreatingUnderParentId(null)}>
                      New location
                    </Button>
                  </Can>
                }
              />
            }
          />
        ) : null}
      </Card>

      {creatingUnderParentId !== undefined ? (
        <LocationFormDialog
          warehouseId={id}
          location={null}
          initialParentId={creatingUnderParentId}
          parentOptions={parentOptions}
          onClose={() => setCreatingUnderParentId(undefined)}
          onSaved={(location) => {
            setCreatingUnderParentId(undefined);
            toast.success(`${location.name} created.`);
            locationsState.reload();
          }}
        />
      ) : null}

      {editing ? (
        <LocationFormDialog
          warehouseId={id}
          location={editing}
          parentOptions={parentOptions}
          onClose={() => setEditing(null)}
          onSaved={(location) => {
            setEditing(null);
            toast.success(`${location.name} saved.`);
            locationsState.reload();
          }}
        />
      ) : null}
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/warehouses"
      className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to warehouses
    </Link>
  );
}
