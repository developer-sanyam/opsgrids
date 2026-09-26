import { useState } from "react";

import { deleteWarehouse, listWarehouses } from "@/api/client";
import type { WarehouseOut } from "@/api/types";
import { warehouseImport } from "@/data/importExport/warehouses";
import { useReloadAfterImport } from "@/data/importExport/useReloadAfterImport";
import {
  Can,
  problemMessage,
  useAsync,
  useCan,
  useImportExport,
  useMutation,
} from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  RowActions,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { ImportExportButton, PlusIcon, useToast, WarehouseIcon } from "@opsgrids/ui";
import { WarehouseFormDialog } from "@/forms/WarehouseFormDialog";

/**
 * Reference data behind every warehouse name an inventory item or purchase order carries.
 * Admin-only writes (`operations.inventory.warehouse.write`) — everyone with inventory read can
 * see the list.
 */
export function WarehousesPage() {
  const toast = useToast();
  const canWrite = useCan("operations.inventory.warehouse.write", "ORG");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<WarehouseOut | null>(null);
  const [deleting, setDeleting] = useState<WarehouseOut | null>(null);

  const loader = () => listWarehouses({ size: 200 });
  const state = useAsync(loader, []);
  const dx = useImportExport(warehouseImport);
  useReloadAfterImport(dx.state.phase, state.reload);

  const remove = useMutation((warehouse: WarehouseOut) => deleteWarehouse(warehouse.id), {
    onSuccess: (_result, warehouse) => {
      setDeleting(null);
      toast.success(`${warehouse.name} deleted.`);
      state.reload();
    },
  });

  const columns: Column<WarehouseOut>[] = [
    {
      key: "code",
      header: "Code",
      render: (w) => <span className="tnum text-xs font-semibold text-muted">{w.code}</span>,
    },
    {
      key: "name",
      header: "Name",
      render: (w) => <span className="font-semibold text-ink">{w.name}</span>,
    },
    {
      key: "location",
      header: "Location",
      render: (w) => <span className="text-ink-soft">{w.location || "—"}</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (w) => (
        <RowActions
          label={w.name}
          actions={[
            { label: "Edit", onSelect: () => setEditing(w), hidden: !canWrite },
            { label: "Delete", tone: "danger", onSelect: () => setDeleting(w), hidden: !canWrite },
          ]}
        />
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Inventory"
        title="Warehouses"
        description="The stocking locations every inventory item and purchase order names."
        actions={
          <div className="flex items-center gap-2">
            <ImportExportButton
              meta={dx.meta}
              state={dx.state}
              actions={dx.actions}
              canImport={canWrite}
              canExport
            />
            <Can permission="operations.inventory.warehouse.write" scope="ORG">
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
                New warehouse
              </Button>
            </Can>
          </div>
        }
      />

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState
            title="Couldn't load warehouses"
            message={problemMessage(state.error)}
            onRetry={state.reload}
          />
        ) : null}
        {state.status === "success" ? (
          <DataTable
            columns={columns}
            rows={state.data.items}
            rowKey={(w) => w.id}
            caption="Warehouses"
            emptyState={
              <EmptyState
                icon={<WarehouseIcon className="size-6" />}
                title="No warehouses yet"
                description="Add the first one — inventory items and purchase orders both need a warehouse to point to."
                action={
                  <Can permission="operations.inventory.warehouse.write" scope="ORG">
                    <Button size="sm" onClick={() => setCreating(true)}>
                      New warehouse
                    </Button>
                  </Can>
                }
              />
            }
          />
        ) : null}
      </Card>

      {creating ? (
        <WarehouseFormDialog
          warehouse={null}
          onClose={() => setCreating(false)}
          onSaved={(warehouse) => {
            setCreating(false);
            toast.success(`${warehouse.name} created.`);
            state.reload();
          }}
        />
      ) : null}

      {editing ? (
        <WarehouseFormDialog
          warehouse={editing}
          onClose={() => setEditing(null)}
          onSaved={(warehouse) => {
            setEditing(null);
            toast.success(`${warehouse.name} saved.`);
            state.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          remove.reset();
        }}
        onConfirm={() => deleting && void remove.mutate(deleting)}
        title="Delete this warehouse?"
        body={`${deleting?.name} is removed from the warehouse directory. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
