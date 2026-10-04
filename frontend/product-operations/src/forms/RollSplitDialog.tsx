import { useRef, useState } from "react";

import { listStock, splitRoll } from "@/api/tracking";
import type { RollOut, RollSplitIn, RollSplitOut, SplitChildIn } from "@/api/trackingTypes";
import { problemMessage, useAsync } from "@opsgrids/client";
import { checkQuantity, sumMilli } from "@/lib/qtyInput";
import { formatQty, milliToQty, qtyToMilli } from "@/lib/quantity";
import { occurredOnError } from "@/lib/dateCheck";
import { today } from "@/lib/now";
import { actionableRows } from "@/lib/rollBuckets";
import type { OnSiteRow } from "@/lib/rollBuckets";
import { STOCK_STATUS_LABEL } from "@/lib/trackingLabels";
import { DateAndNoteFields } from "@/tracking/DateAndNoteFields";
import { ShortfallAlert } from "@/tracking/ShortfallAlert";
import { StatusDialog } from "@/tracking/StatusDialog";
import { useStockOperation } from "@/tracking/useStockOperation";
import { Button, EmptyState, ErrorState, FormDialog, Select, Skeleton, TextField } from "@/ui";
import { PlusIcon } from "@opsgrids/ui";

const MAX_CHILDREN = 50;
const MIN_CHILDREN = 2;

interface RollSplitDialogProps {
  roll: RollOut;
  onClose: () => void;
  onSaved: (result: RollSplitOut) => void;
}

/**
 * Split a roll — `POST /operations/inventory/rolls/{id}:split` (`operations.inventory.roll.write`).
 *
 * The roll's stock is read first (`GET …/stock?roll_id=`), because the children must add up
 * **exactly** to what the bucket holds and the person needs that figure in front of them. While it
 * loads, fails, or finds nothing to split, a plain dialog says so — there is no submit button that
 * could do nothing. Once the figure is known, a live total shows how much is left to allocate (or
 * how far over), and the form refuses to send until the sum is exact. The server re-checks and its
 * `children/split_total` message, which carries both numbers, is shown as-is if it disagrees.
 */
export function RollSplitDialog({ roll, onClose, onSaved }: RollSplitDialogProps) {
  const stock = useAsync(() => listStock({ roll_id: roll.id, limit: 200 }), [roll.id]);
  const title = `Split roll ${roll.roll_no}`;

  if (stock.status === "loading") {
    return (
      <StatusDialog title={title} onClose={onClose}>
        <p className="sr-only">Loading the stock this roll holds…</p>
        <Skeleton className="h-12 w-full rounded-tile" />
      </StatusDialog>
    );
  }
  if (stock.status === "error") {
    return (
      <StatusDialog title={title} onClose={onClose}>
        <ErrorState
          title="Couldn't load this roll's stock"
          message={problemMessage(stock.error)}
          onRetry={stock.reload}
        />
      </StatusDialog>
    );
  }
  const buckets = actionableRows(stock.data.items);
  if (roll.state !== "ACTIVE" || buckets.length === 0) {
    return (
      <StatusDialog title={title} onClose={onClose}>
        <EmptyState
          title="Nothing to split"
          description={
            roll.state !== "ACTIVE"
              ? "This roll has already been split or merged."
              : "This roll holds no stock. Post opening stock into it first, then split it."
          }
        />
      </StatusDialog>
    );
  }
  return <RollSplitForm roll={roll} buckets={buckets} onClose={onClose} onSaved={onSaved} />;
}

interface ChildRow {
  key: number;
  quantity: string;
  rollNo: string;
}

interface RollSplitFormProps {
  roll: RollOut;
  buckets: OnSiteRow[];
  onClose: () => void;
  onSaved: (result: RollSplitOut) => void;
}

function RollSplitForm({ roll, buckets, onClose, onSaved }: RollSplitFormProps) {
  const nextKey = useRef(MIN_CHILDREN);
  const [bucketId, setBucketId] = useState(buckets[0]?.id ?? "");
  const [rows, setRows] = useState<ChildRow[]>(() =>
    Array.from({ length: MIN_CHILDREN }, (_, key) => ({ key, quantity: "", rollNo: "" })),
  );
  const [occurredOn, setOccurredOn] = useState(today());
  const [note, setNote] = useState("");
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  const [totalError, setTotalError] = useState<string | undefined>(undefined);
  const [dateError, setDateError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const op = useStockOperation(
    (input: RollSplitIn, key: string) => splitRoll(roll.id, input, key),
    onSaved,
    ["children", "occurred_on", "note"],
  );

  const bucket = buckets.find((b) => b.id === bucketId) ?? buckets[0];
  const heldMilli = bucket ? qtyToMilli(bucket.on_hand_qty) : 0;
  const allocatedMilli = sumMilli(rows.map((r) => r.quantity));
  const leftMilli = heldMilli - allocatedMilli;

  const dirty = rows.some((r) => r.quantity !== "" || r.rollNo !== "") || rows.length !== MIN_CHILDREN ||
    note !== "" || occurredOn !== today();

  function update(key: number, patch: Partial<ChildRow>) {
    setRows((current) => current.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  function submit() {
    if (!bucket) return;
    const errors: Record<number, string> = {};
    const children: SplitChildIn[] = [];
    for (const row of rows) {
      const checked = checkQuantity(row.quantity, { uom: roll.uom });
      if (!checked.ok) {
        errors[row.key] = checked.message;
        continue;
      }
      const rollNo = row.rollNo.trim();
      children.push({ quantity: checked.value, ...(rollNo ? { roll_no: rollNo } : {}) });
    }
    setRowErrors(errors);
    const dateProblem = occurredOnError(occurredOn);
    setDateError(dateProblem ?? undefined);

    let total: string | undefined;
    if (Object.keys(errors).length === 0 && allocatedMilli !== heldMilli) {
      total = `The children add up to ${formatQty(milliToQty(allocatedMilli))} ${roll.uom}, but this roll holds ${formatQty(bucket.on_hand_qty)} ${roll.uom}. They must match exactly.`;
    }
    setTotalError(total);
    if (Object.keys(errors).length > 0 || total || dateProblem) {
      if (Object.keys(errors).length > 0) firstFieldRef.current?.focus();
      return;
    }
    op.submit({
      location_id: bucket.location_id,
      status: bucket.status,
      children,
      occurred_on: occurredOn,
      ...(note.trim() ? { note: note.trim() } : {}),
    });
  }

  const childrenError = totalError ?? op.fieldErrors["children"];
  const exact = leftMilli === 0 && allocatedMilli > 0;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Split roll ${roll.roll_no}`}
      description={`${roll.sku}${roll.lot_no ? ` · lot ${roll.lot_no}` : ""} · ${formatQty(roll.on_hand_qty)} ${roll.uom} on hand`}
      submitLabel="Split roll"
      pendingLabel="Splitting…"
      pending={op.pending}
      initialFocusRef={firstFieldRef}
      size="lg"
      dirty={dirty}
      {...op.dialogError}
    >
      {buckets.length > 1 ? (
        <Select
          label="Take stock from"
          value={bucketId}
          onChange={(e) => setBucketId(e.target.value)}
          options={buckets.map((b) => ({
            value: b.id,
            label: `${b.location_code} · ${STOCK_STATUS_LABEL[b.status]} — ${formatQty(b.on_hand_qty)} ${b.uom}`,
          }))}
          required
        />
      ) : bucket ? (
        <p className="text-sm text-ink-soft">
          Splitting the {formatQty(bucket.on_hand_qty)} {bucket.uom} at{" "}
          <span className="font-semibold text-ink">{bucket.location_code}</span> (
          {STOCK_STATUS_LABEL[bucket.status]}).
        </p>
      ) : null}

      <fieldset
        className="m-0 flex flex-col gap-3 border-0 p-0"
        aria-describedby="split-children-total"
      >
        <legend className="mb-1 text-body-sm font-semibold text-ink-soft">Children</legend>
        <ul className="flex flex-col gap-3">
          {rows.map((row, index) => (
            <li key={row.key} className="grid items-start gap-3 sm:grid-cols-2">
              <TextField
                {...(index === 0 ? { ref: firstFieldRef } : {})}
                label={`Child ${index + 1} quantity (${roll.uom})`}
                inputMode="decimal"
                value={row.quantity}
                onChange={(e) => update(row.key, { quantity: e.target.value })}
                required
                error={rowErrors[row.key]}
              />
              <TextField
                label={`Child ${index + 1} roll number`}
                value={row.rollNo}
                onChange={(e) => update(row.key, { rollNo: e.target.value })}
                optional
                placeholder={`${roll.roll_no}-${index + 1}`}
              />
              {rows.length > MIN_CHILDREN ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-fit sm:col-span-2"
                  onClick={() => setRows((current) => current.filter((r) => r.key !== row.key))}
                  aria-label={`Remove child ${index + 1}`}
                >
                  Remove
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
        {rows.length < MAX_CHILDREN ? (
          <Button
            variant="outline"
            size="sm"
            className="w-fit"
            leadingIcon={<PlusIcon className="size-4" />}
            onClick={() => {
              const key = nextKey.current;
              nextKey.current += 1;
              setRows((current) => [...current, { key, quantity: "", rollNo: "" }]);
            }}
          >
            Add child
          </Button>
        ) : null}
        <p
          id="split-children-total"
          aria-live="polite"
          className={`tnum rounded-tile bg-ground px-4 py-3 text-sm font-semibold ${
            exact ? "text-present" : "text-ink"
          }`}
        >
          Children total {formatQty(milliToQty(allocatedMilli))} of {formatQty(milliToQty(heldMilli))}{" "}
          {roll.uom}.{" "}
          {exact
            ? "Matches exactly."
            : leftMilli > 0
              ? `${formatQty(milliToQty(leftMilli))} ${roll.uom} left to allocate.`
              : `Over by ${formatQty(milliToQty(-leftMilli))} ${roll.uom}.`}
        </p>
        {childrenError ? (
          <p role="alert" className="text-sm text-negative">
            {childrenError}
          </p>
        ) : null}
      </fieldset>

      <DateAndNoteFields
        occurredOn={occurredOn}
        onOccurredOn={setOccurredOn}
        note={note}
        onNote={setNote}
        dateError={dateError ?? op.fieldErrors["occurred_on"]}
        noteError={op.fieldErrors["note"]}
      />
      <ShortfallAlert shortfall={op.shortfall} />
    </FormDialog>
  );
}
