import { useMemo, useState } from "react";

import { changeStockStatus, listStockFor, resolveScan, transferStock } from "@/api/scan";
import type { OnSiteStatus, ScanEntityOut, StockRow } from "@/api/scan";
import { formatQty, qtyToMilli } from "@/lib/quantity";
import { checkQuantity } from "@/lib/stockQty";
import { problemMessage, useAsync, useMutation } from "@opsgrids/client";
import { Button, EmptyState, ErrorState, InlineAlert, Select, Skeleton, TextField } from "@/ui";
import { BarcodeScanner } from "./BarcodeScanner";
import { useRequestKey } from "./useRequestKey";

/** Statuses stock can be moved between where it sits (the server's `OnSiteStatus`). */
const STATUS_OPTIONS: { value: OnSiteStatus; label: string }[] = [
  { value: "AVAILABLE", label: "Available" },
  { value: "QUALITY_HOLD", label: "Quality hold" },
  { value: "QUARANTINE", label: "Quarantine" },
  { value: "DAMAGED", label: "Damaged" },
  { value: "REJECTED", label: "Rejected" },
  { value: "BLOCKED", label: "Blocked" },
];
const MOVABLE = new Set<string>(STATUS_OPTIONS.map((o) => o.value).concat(["WIP"]));

function bucketLabel(b: StockRow): string {
  const what = b.roll_no ?? b.lot_no ?? b.sku;
  return `${what} · ${b.warehouse_code}/${b.location_code} · ${b.status.replace("_", " ").toLowerCase()} · ${formatQty(b.on_hand_qty)} ${b.uom}`;
}

interface PanelProps {
  entity: ScanEntityOut;
  onDone: (message: string) => void;
  onCancel: () => void;
}

/** Pick which bucket of the scanned thing is being moved. */
function BucketPicker({
  buckets,
  value,
  onChange,
}: {
  buckets: StockRow[];
  value: string;
  onChange: (id: string) => void;
}) {
  if (buckets.length === 1) {
    return <p className="text-base font-semibold text-ink">{bucketLabel(buckets[0] as StockRow)}</p>;
  }
  return (
    <Select
      label="From"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      options={buckets.map((b) => ({ value: b.id, label: bucketLabel(b) }))}
      className="min-h-14 text-base"
    />
  );
}

function useBuckets(entity: ScanEntityOut) {
  const state = useAsync(() => listStockFor(entity), [entity.id]);
  const buckets = useMemo(
    () =>
      state.status === "success"
        ? state.data.filter((b) => qtyToMilli(b.available_qty) > 0 && MOVABLE.has(b.status))
        : [],
    [state],
  );
  return { state, buckets };
}

/** Move stock bin to bin: scan or type the destination bin, confirm the quantity. */
export function TransferPanel({ entity, onDone, onCancel }: PanelProps) {
  const { state, buckets } = useBuckets(entity);
  const [bucketId, setBucketId] = useState("");
  const [quantity, setQuantity] = useState<string | null>(null);
  const [destination, setDestination] = useState<ScanEntityOut | null>(null);
  const [destinationError, setDestinationError] = useState("");
  const [looking, setLooking] = useState(false);
  const keyFor = useRequestKey();

  const bucket = buckets.find((b) => b.id === bucketId) ?? buckets[0];
  const qty = quantity ?? bucket?.available_qty ?? "";
  const qtyError = bucket && quantity !== null ? checkQuantity(qty, bucket) : "";

  const save = useMutation(
    (input: Parameters<typeof transferStock>[0]) => transferStock(input, keyFor(input)),
    {
      onSuccess: () =>
        onDone(`Moved ${qty} ${bucket?.uom ?? ""} to ${destination?.title ?? "the new bin"}.`),
    },
  );

  const lookUpDestination = async (text: string) => {
    setLooking(true);
    setDestinationError("");
    try {
      const out = await resolveScan(text);
      if (out.type !== "location") {
        setDestination(null);
        setDestinationError("That label is not a bin. Scan the label on the shelf or bin.");
      } else if (bucket && out.entity.warehouse_id !== bucket.warehouse_id) {
        setDestination(null);
        setDestinationError(
          "That bin is in another warehouse. Moves between warehouses are not available yet.",
        );
      } else {
        setDestination(out.entity);
      }
    } catch (error) {
      setDestination(null);
      setDestinationError(problemMessage(error));
    } finally {
      setLooking(false);
    }
  };

  if (state.status === "loading") return <Skeleton className="h-40 w-full" />;
  if (state.status === "error") {
    return <ErrorState title="Couldn't load the stock" message={problemMessage(state.error)} onRetry={state.reload} />;
  }
  if (!bucket) {
    return (
      <EmptyState
        title="Nothing free to move"
        description="No stock of this is available here — it may be reserved, on hold, or already moved."
        action={<Button variant="secondary" onClick={onCancel}>Back</Button>}
      />
    );
  }

  const ready = destination !== null && qtyError === "" && qty !== "";
  const submit = () => {
    if (!destination || !ready) return;
    void save.mutate({
      inventory_item_id: bucket.inventory_item_id,
      from_location_id: bucket.location_id,
      to_location_id: destination.id,
      quantity: qty.trim(),
      status: bucket.status as OnSiteStatus,
      lot_id: bucket.lot_id,
      roll_id: bucket.roll_id,
    });
  };

  return (
    <section aria-label="Move stock" className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold">Move</h2>
      <BucketPicker buckets={buckets} value={bucket.id} onChange={setBucketId} />
      <TextField
        label={`Quantity (${bucket.uom})`}
        value={qty}
        onChange={(e) => setQuantity(e.target.value)}
        inputMode="decimal"
        autoComplete="off"
        error={qtyError || undefined}
        className="min-h-14 text-lg"
      />
      {destination ? (
        <InlineAlert tone="success" title={`To ${destination.title}`}>
          {destination.subtitle}
        </InlineAlert>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="text-base font-semibold">Scan the destination bin</p>
          <BarcodeScanner onScan={(text) => void lookUpDestination(text)} busy={looking} />
          {destinationError ? (
            <InlineAlert tone="danger" title={destinationError} />
          ) : null}
        </div>
      )}
      {save.error ? <InlineAlert tone="danger" title={problemMessage(save.error)} /> : null}
      <div className="flex flex-col gap-3 sm:flex-row-reverse">
        <Button
          className="min-h-14 w-full text-base"
          onClick={submit}
          disabled={!ready}
          loading={save.pending}
        >
          Confirm move
        </Button>
        <Button variant="secondary" className="min-h-14 w-full text-base" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </section>
  );
}

/** Put stock on hold / release it, where it sits. */
export function StatusPanel({ entity, onDone, onCancel }: PanelProps) {
  const { state, buckets } = useBuckets(entity);
  const [bucketId, setBucketId] = useState("");
  const [target, setTarget] = useState<OnSiteStatus | "">("");
  const [quantity, setQuantity] = useState<string | null>(null);
  const keyFor = useRequestKey();

  const bucket = buckets.find((b) => b.id === bucketId) ?? buckets[0];
  const qty = quantity ?? bucket?.available_qty ?? "";
  const qtyError = bucket && quantity !== null ? checkQuantity(qty, bucket) : "";
  const to: OnSiteStatus =
    target !== "" && target !== bucket?.status
      ? target
      : bucket?.status === "AVAILABLE"
        ? "QUALITY_HOLD"
        : "AVAILABLE";

  const save = useMutation(
    (input: Parameters<typeof changeStockStatus>[0]) => changeStockStatus(input, keyFor(input)),
    {
      onSuccess: () =>
        onDone(`${qty} ${bucket?.uom ?? ""} is now ${to.replace("_", " ").toLowerCase()}.`),
    },
  );

  if (state.status === "loading") return <Skeleton className="h-40 w-full" />;
  if (state.status === "error") {
    return <ErrorState title="Couldn't load the stock" message={problemMessage(state.error)} onRetry={state.reload} />;
  }
  if (!bucket) {
    return (
      <EmptyState
        title="Nothing free to change"
        description="No stock of this is available to change here."
        action={<Button variant="secondary" onClick={onCancel}>Back</Button>}
      />
    );
  }
  const ready = qtyError === "" && qty !== "";
  return (
    <section aria-label="Hold or release stock" className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold">Hold or release</h2>
      <BucketPicker buckets={buckets} value={bucket.id} onChange={setBucketId} />
      <Select
        label="Change to"
        value={to}
        onChange={(e) => setTarget(e.target.value as OnSiteStatus)}
        options={STATUS_OPTIONS.filter((o) => o.value !== bucket.status)}
        className="min-h-14 text-base"
      />
      <TextField
        label={`Quantity (${bucket.uom})`}
        value={qty}
        onChange={(e) => setQuantity(e.target.value)}
        inputMode="decimal"
        autoComplete="off"
        error={qtyError || undefined}
        className="min-h-14 text-lg"
      />
      {save.error ? <InlineAlert tone="danger" title={problemMessage(save.error)} /> : null}
      <div className="flex flex-col gap-3 sm:flex-row-reverse">
        <Button
          className="min-h-14 w-full text-base"
          disabled={!ready}
          loading={save.pending}
          onClick={() => {
            if (!ready) return;
            void save.mutate({
              inventory_item_id: bucket.inventory_item_id,
              location_id: bucket.location_id,
              from_status: bucket.status as OnSiteStatus,
              to_status: to,
              quantity: qty.trim(),
              lot_id: bucket.lot_id,
              roll_id: bucket.roll_id,
            });
          }}
        >
          Confirm
        </Button>
        <Button variant="secondary" className="min-h-14 w-full text-base" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </section>
  );
}
