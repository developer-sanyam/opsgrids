import { useRef, useState } from "react";

import { listStock, mergeRolls } from "@/api/tracking";
import type { RollMergeIn, RollMergeOut, RollOut, StockRowOut } from "@/api/trackingTypes";
import { problemMessage, useAsync } from "@opsgrids/client";
import { formatQty, milliToQty } from "@/lib/quantity";
import { occurredOnError } from "@/lib/dateCheck";
import { today } from "@/lib/now";
import { commonBuckets, mergeBlocker } from "@/lib/rollBuckets";
import { STOCK_STATUS_LABEL } from "@/lib/trackingLabels";
import { DateAndNoteFields } from "@/tracking/DateAndNoteFields";
import { ShortfallAlert } from "@/tracking/ShortfallAlert";
import { StatusDialog } from "@/tracking/StatusDialog";
import { useStockOperation } from "@/tracking/useStockOperation";
import { EmptyState, ErrorState, FormDialog, InlineAlert, Select, Skeleton, TextField } from "@/ui";

const ROLL_NO_PATTERN = /^[A-Za-z0-9._/-]+$/;

interface RollMergeDialogProps {
  /** The rolls the person selected, at least two. */
  rolls: RollOut[];
  onClose: () => void;
  onSaved: (result: RollMergeOut) => void;
}

/**
 * Merge rolls — `POST /operations/inventory/rolls:merge` (`operations.inventory.roll.write`): n
 * rolls of one item, lot and shade, all active and all holding stock in the same location and
 * status, become one new roll holding their combined quantity.
 *
 * What the client can see is checked up front (same item, lot and shade, all active) and the
 * buckets every roll shares are computed from their stock, so the dialog only offers a merge that
 * can work and says plainly why when none can. The server decides regardless: its
 * `roll_ids/not_mergeable` refusal is put on the selection, a `roll_no` clash on the number field.
 */
export function RollMergeDialog({ rolls, onClose, onSaved }: RollMergeDialogProps) {
  const blocker = mergeBlocker(rolls);
  const stock = useAsync(
    async () => {
      // A selection the client already knows cannot merge reads no stock.
      if (blocker) return new Map<string, StockRowOut[]>();
      const pages = await Promise.all(rolls.map((r) => listStock({ roll_id: r.id, limit: 200 })));
      return new Map<string, StockRowOut[]>(rolls.map((r, i) => [r.id, pages[i]?.items ?? []]));
    },
    [rolls.map((r) => r.id).join(","), blocker],
  );
  const title = `Merge ${rolls.length} rolls`;

  if (blocker) {
    return (
      <StatusDialog title={title} onClose={onClose}>
        <EmptyState title="These rolls can't be merged" description={blocker} />
      </StatusDialog>
    );
  }
  if (stock.status === "loading") {
    return (
      <StatusDialog title={title} onClose={onClose}>
        <p className="sr-only">Loading the stock these rolls hold…</p>
        <Skeleton className="h-12 w-full rounded-tile" />
      </StatusDialog>
    );
  }
  if (stock.status === "error") {
    return (
      <StatusDialog title={title} onClose={onClose}>
        <ErrorState
          title="Couldn't load these rolls' stock"
          message={problemMessage(stock.error)}
          onRetry={stock.reload}
        />
      </StatusDialog>
    );
  }
  const buckets = commonBuckets(rolls, stock.data);
  if (buckets.length === 0) {
    return (
      <StatusDialog title={title} onClose={onClose}>
        <EmptyState
          title="These rolls share no location"
          description="Every roll must hold stock in the same location and status. Transfer them together first, then merge."
        />
      </StatusDialog>
    );
  }
  return <RollMergeForm rolls={rolls} buckets={buckets} onClose={onClose} onSaved={onSaved} />;
}

interface RollMergeFormProps {
  rolls: RollOut[];
  buckets: ReturnType<typeof commonBuckets>;
  onClose: () => void;
  onSaved: (result: RollMergeOut) => void;
}

function RollMergeForm({ rolls, buckets, onClose, onSaved }: RollMergeFormProps) {
  const [bucketKey, setBucketKey] = useState(buckets[0]?.key ?? "");
  const [rollNo, setRollNo] = useState("");
  const [occurredOn, setOccurredOn] = useState(today());
  const [note, setNote] = useState("");
  const [rollNoError, setRollNoError] = useState<string | undefined>(undefined);
  const [dateError, setDateError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const op = useStockOperation(mergeRolls, onSaved, ["roll_ids", "location_id", "roll_no", "occurred_on", "note"]);
  const bucket = buckets.find((b) => b.key === bucketKey) ?? buckets[0];
  const first = rolls[0];
  const uom = first?.uom ?? "";
  const dirty = rollNo !== "" || note !== "" || occurredOn !== today() || bucketKey !== (buckets[0]?.key ?? "");

  function submit() {
    if (!bucket) return;
    setRollNoError(undefined);
    setDateError(undefined);
    const trimmed = rollNo.trim();
    let valid = true;
    if (trimmed !== "" && (trimmed.length > 60 || !ROLL_NO_PATTERN.test(trimmed))) {
      setRollNoError("Letters, numbers, dots, slashes, hyphens and underscores only, up to 60.");
      firstFieldRef.current?.focus();
      valid = false;
    }
    const dateProblem = occurredOnError(occurredOn);
    if (dateProblem) {
      setDateError(dateProblem);
      valid = false;
    }
    if (!valid) return;
    const input: RollMergeIn = {
      roll_ids: rolls.map((r) => r.id),
      location_id: bucket.location_id,
      status: bucket.status,
      occurred_on: occurredOn,
      ...(trimmed ? { roll_no: trimmed } : {}),
      ...(note.trim() ? { note: note.trim() } : {}),
    };
    op.submit(input);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Merge ${rolls.length} rolls`}
      description={`${first?.sku ?? ""}${first?.lot_no ? ` · lot ${first.lot_no}` : ""}`}
      submitLabel="Merge rolls"
      pendingLabel="Merging…"
      pending={op.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      {...op.dialogError}
    >
      <div>
        <p className="text-sm font-semibold text-ink-soft">Rolls being merged</p>
        <ul className="mt-1 flex flex-wrap gap-2 text-sm text-ink">
          {rolls.map((r) => (
            <li key={r.id} className="rounded-chip bg-ground px-2 py-1">
              {r.roll_no}
            </li>
          ))}
        </ul>
      </div>
      {op.fieldErrors["location_id"] ? (
        <InlineAlert tone="danger" title={op.fieldErrors["location_id"]} />
      ) : null}
      {op.fieldErrors["roll_ids"] ? (
        <InlineAlert tone="danger" title={op.fieldErrors["roll_ids"]} />
      ) : null}
      {buckets.length > 1 ? (
        <Select
          label="Merge at"
          value={bucketKey}
          onChange={(e) => setBucketKey(e.target.value)}
          options={buckets.map((b) => ({
            value: b.key,
            label: `${b.location_code} · ${STOCK_STATUS_LABEL[b.status]} — ${formatQty(milliToQty(b.totalMilli))} ${uom}`,
          }))}
          required
        />
      ) : bucket ? (
        <p className="text-sm text-ink-soft">
          Merging at <span className="font-semibold text-ink">{bucket.location_code}</span> (
          {STOCK_STATUS_LABEL[bucket.status]}).
        </p>
      ) : null}
      {bucket ? (
        <p className="tnum rounded-tile bg-ground px-4 py-3 text-sm font-semibold text-ink" aria-live="polite">
          The new roll will hold {formatQty(milliToQty(bucket.totalMilli))} {uom}.
        </p>
      ) : null}
      <TextField
        ref={firstFieldRef}
        label="New roll number"
        value={rollNo}
        onChange={(e) => setRollNo(e.target.value)}
        optional
        helper="Leave blank to generate one."
        error={rollNoError ?? op.fieldErrors["roll_no"]}
      />
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
