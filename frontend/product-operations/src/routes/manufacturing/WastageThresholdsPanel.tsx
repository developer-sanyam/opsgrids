import { useState } from "react";

import { listWastageThresholds, removeWastageThreshold } from "@/api/client";
import type { WastageThresholdOut } from "@/api/types";
import { WASTAGE_TYPE_LABEL } from "@/data/wastageTypes";
import { WastageThresholdDialog } from "@/forms/WastageThresholdDialog";
import { formatPercent } from "@/lib/quantity";
import { Can, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import { Button, Card, ConfirmDialog, DataTable, EmptyState, ErrorState, SectionHeader, Skeleton } from "@/ui";
import type { Column } from "@/ui";

/**
 * Wastage thresholds. Everyone who can read production orders sees the list; adding and removing
 * is `operations.manufacturing.bom.write` (the server enforces it, this only hides the controls).
 */
export function WastageThresholdsPanel({ canWrite }: { canWrite: boolean }) {
  const state = useAsync(() => listWastageThresholds(), []);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<WastageThresholdOut | null>(null);

  const remove = useMutation((t: WastageThresholdOut) => removeWastageThreshold(t.id), {
    onSuccess: () => {
      setRemoving(null);
      state.reload();
    },
  });

  const columns: Column<WastageThresholdOut>[] = [
    {
      key: "item",
      header: "Produced item",
      render: (t) => (t.sku ? <span className="font-semibold text-ink">{t.sku}</span> : <span className="text-muted">Every product</span>),
    },
    {
      key: "type",
      header: "Wastage type",
      render: (t) => (t.wastage_type ? WASTAGE_TYPE_LABEL[t.wastage_type] : <span className="text-muted">Every type</span>),
    },
    {
      key: "max",
      header: "Maximum",
      align: "right",
      render: (t) => <span className="tnum">{formatPercent(t.max_percent)}</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (t) =>
        canWrite ? (
          <Button
            size="sm"
            variant="secondary"
            aria-label={`Remove threshold for ${t.sku ?? "every product"}, ${t.wastage_type ? WASTAGE_TYPE_LABEL[t.wastage_type] : "every type"}`}
            onClick={() => setRemoving(t)}
          >
            Remove
          </Button>
        ) : null,
    },
  ];

  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
      <SectionHeader
        title="Wastage thresholds"
        subtitle="An alert is raised when wastage goes past a limit."
        action={
          <Can permission="operations.manufacturing.bom.write" scope="ORG">
            <Button size="sm" variant="secondary" onClick={() => setAdding(true)}>
              Add threshold
            </Button>
          </Can>
        }
      />
      {state.status === "loading" ? <Skeleton className="h-24 w-full rounded-tile" /> : null}
      {state.status === "error" ? (
        <ErrorState title="Couldn't load wastage thresholds" message={problemMessage(state.error)} onRetry={state.reload} />
      ) : null}
      {state.status === "success" ? (
        <DataTable
          columns={columns}
          rows={state.data}
          rowKey={(t) => t.id}
          caption="Wastage thresholds"
          emptyState={
            <EmptyState
              title="No wastage thresholds"
              description={canWrite ? "Add one to be alerted when wastage runs high." : "None have been set up yet."}
            />
          }
        />
      ) : null}

      {adding ? (
        <WastageThresholdDialog
          onClose={() => setAdding(false)}
          onSaved={() => {
            setAdding(false);
            state.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={removing !== null}
        onClose={() => {
          setRemoving(null);
          remove.reset();
        }}
        onConfirm={() => removing && remove.mutate(removing)}
        title="Remove this threshold?"
        body="Wastage this limit covered stops raising an alert unless another threshold applies."
        confirmLabel="Remove threshold"
        pendingLabel="Removing…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </Card>
  );
}
