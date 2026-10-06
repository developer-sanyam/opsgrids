import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getWarehouseById, listWarehouseFloors } from "@/api/client";
import type { WarehouseFloorOut } from "@/api/types";
import { Can, problemMessage, useAsync, useCan } from "@opsgrids/client";
import { Button, Card, DataTable, EmptyState, ErrorState, PageHeader, RowActions, Skeleton } from "@/ui";
import type { Column } from "@/ui";
import { ArrowRightIcon, LayersIcon, PlusIcon, useToast } from "@opsgrids/ui";
import { WarehouseFloorFormDialog } from "@/forms/WarehouseFloorFormDialog";

/**
 * A warehouse's floors (Fulfilment plan §2/F7) — the thing each has its own versioned layout of.
 * `operations.warehouse.floor.write` gates creating and renaming; every floor links to its own
 * layout editor, which is where the geometry itself is authored.
 */
export function WarehouseFloorsPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const canWrite = useCan("operations.warehouse.floor.write", "ORG");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<WarehouseFloorOut | null>(null);

  const warehouseState = useAsync(() => getWarehouseById(id), [id]);
  const floorsState = useAsync(() => listWarehouseFloors(id), [id]);

  const columns: Column<WarehouseFloorOut>[] = [
    {
      key: "floor_number",
      header: "Floor",
      mobile: "title",
      render: (f) => (
        <span className="tnum font-semibold text-ink">
          {f.floor_number}
          {f.name ? <span className="ml-2 font-normal text-ink-soft">{f.name}</span> : null}
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (f) => (
        <RowActions
          label={f.name || `Floor ${f.floor_number}`}
          actions={[
            {
              label: "View floor",
              onSelect: () => navigate(`/warehouses/${id}/floors/${f.id}/view`),
            },
            {
              label: "Edit layout",
              onSelect: () => navigate(`/warehouses/${id}/floors/${f.id}`),
            },
            { label: "Rename", onSelect: () => setEditing(f), hidden: !canWrite },
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
        eyebrow="Floors"
        title={warehouseState.status === "success" ? warehouseState.data.name : "Loading…"}
        description="Each floor has its own versioned layout: where locations sit, and the walk graph routing is computed over."
        actions={
          <Can permission="operations.warehouse.floor.write" scope="ORG">
            <Button
              leadingIcon={<PlusIcon className="size-4" />}
              onClick={() => setCreating(true)}
              disabled={warehouseState.status !== "success"}
            >
              New floor
            </Button>
          </Can>
        }
      />

      <Card className="p-2 sm:p-4">
        {floorsState.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {floorsState.status === "error" ? (
          <ErrorState
            title="Couldn't load floors"
            message={problemMessage(floorsState.error)}
            onRetry={floorsState.reload}
          />
        ) : null}
        {floorsState.status === "success" ? (
          <DataTable
            columns={columns}
            rows={floorsState.data}
            rowKey={(f) => f.id}
            caption={
              warehouseState.status === "success" ? `Floors in ${warehouseState.data.name}` : "Floors"
            }
            mobileCard="auto"
            emptyState={
              <EmptyState
                icon={<LayersIcon className="size-6" />}
                title="No floors yet"
                description="Add a floor to start authoring its layout — where locations sit, and the walk graph a picker's route is computed over."
                action={
                  <Can permission="operations.warehouse.floor.write" scope="ORG">
                    <Button size="sm" onClick={() => setCreating(true)}>
                      New floor
                    </Button>
                  </Can>
                }
              />
            }
          />
        ) : null}
      </Card>

      {creating ? (
        <WarehouseFloorFormDialog
          warehouseId={id}
          floor={null}
          onClose={() => setCreating(false)}
          onSaved={(floor) => {
            setCreating(false);
            toast.success(`Floor ${floor.floor_number} created.`);
            floorsState.reload();
          }}
        />
      ) : null}

      {editing ? (
        <WarehouseFloorFormDialog
          warehouseId={id}
          floor={editing}
          onClose={() => setEditing(null)}
          onSaved={(floor) => {
            setEditing(null);
            toast.success(`Floor ${floor.floor_number} saved.`);
            floorsState.reload();
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
