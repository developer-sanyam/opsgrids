import { useState } from "react";

import { deleteUomConversion, listUomConversions } from "@/api/client";
import type { UomConversionOut } from "@/api/types";
import { UomConversionDialog } from "@/forms/UomConversionDialog";
import { Can, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  LoadError,
  RowActions,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { BoxIcon, PlusIcon, useToast } from "@opsgrids/ui";

const WRITE = "operations.inventory.item.write";

function ratio(c: UomConversionOut): string {
  return `1 ${c.uom} = ${c.denominator === 1 ? c.numerator : `${c.numerator}/${c.denominator}`} ${c.stock_uom}`;
}

/**
 * An item's own unit conversions ("1 box = 12 pcs"), which let a delivery be entered in the unit it
 * arrived in. Reading needs the item read permission the screen already has; changing them needs
 * `operations.inventory.item.write` — a UI gate only, the endpoints refuse on their own. A delete
 * the server refuses (an open purchase order still orders in that unit) shows the server's sentence.
 */
export function UomConversionsCard({ itemId, stockUom }: { itemId: string; stockUom: string }) {
  const toast = useToast();
  const state = useAsync(() => listUomConversions(itemId), [itemId]);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<UomConversionOut | null>(null);
  const [deleting, setDeleting] = useState<UomConversionOut | null>(null);

  const remove = useMutation((c: UomConversionOut) => deleteUomConversion(c.id), {
    onSuccess: (_result, c) => {
      setDeleting(null);
      toast.success(`${c.uom} conversion removed.`);
      state.reload();
    },
  });

  const columns: Column<UomConversionOut>[] = [
    { key: "ratio", header: "Conversion", render: (c) => <span className="tnum font-semibold text-ink">{ratio(c)}</span> },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (c) => (
        <Can permission={WRITE} scope="ORG">
          <RowActions
            label={`${c.uom} conversion`}
            actions={[
              { label: "Edit", onSelect: () => setEditing(c) },
              { label: "Remove", tone: "danger", onSelect: () => setDeleting(c) },
            ]}
          />
        </Can>
      ),
    },
  ];

  return (
    <Card className="p-2 sm:p-4">
      <div className="px-3 pt-2 pb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <SectionHeader title="Unit conversions" subtitle={`How many ${stockUom} a box, carton or bundle holds.`} />
        <Can permission={WRITE} scope="ORG">
          <Button size="sm" variant="outline" leadingIcon={<PlusIcon className="size-4" />} onClick={() => setAdding(true)}>
            Add conversion
          </Button>
        </Can>
      </div>
      {state.status === "loading" ? (
        <div className="flex flex-col gap-3 p-3" role="status" aria-label="Loading unit conversions">
          <Skeleton className="h-12 w-full rounded-tile" />
        </div>
      ) : null}
      {state.status === "error" ? (
        <LoadError error={state.error} noun="unit conversions" onRetry={state.reload} />
      ) : null}
      {state.status === "success" ? (
        <DataTable
          columns={columns}
          rows={state.data}
          rowKey={(c) => c.id}
          caption="Unit conversions"
          emptyState={
            <EmptyState
              icon={<BoxIcon className="size-6" />}
              title="No unit conversions yet"
              description="Add one so a delivery of boxes can be booked in the item's own unit."
              action={
                <Can permission={WRITE} scope="ORG">
                  <Button size="sm" onClick={() => setAdding(true)}>
                    Add conversion
                  </Button>
                </Can>
              }
            />
          }
        />
      ) : null}

      {adding ? (
        <UomConversionDialog
          itemId={itemId}
          stockUom={stockUom}
          conversion={null}
          onClose={() => setAdding(false)}
          onSaved={(saved) => {
            setAdding(false);
            toast.success(`${ratio(saved)} added.`);
            state.reload();
          }}
        />
      ) : null}
      {editing ? (
        <UomConversionDialog
          itemId={itemId}
          stockUom={stockUom}
          conversion={editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(null);
            toast.success(`${ratio(saved)} saved.`);
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
        title="Remove this conversion?"
        body={`${deleting ? ratio(deleting) : ""} is removed. Receipts already posted keep what they booked.`}
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </Card>
  );
}
