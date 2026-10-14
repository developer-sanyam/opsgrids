import { useId } from "react";

import type { ReceivingLineOut } from "@/api/types";
import { formatQty, qtyToMilli } from "@/lib/quantity";
import { effectiveHold } from "@/lib/receiptForm";
import type { LineAnalysis, ReceiptLineDraft, ServerLineProblems } from "@/lib/receiptForm";
import { InlineAlert, Select, Switch, TextField } from "@/ui";
import type { SelectOption } from "@/ui";
import { ReceiveRollsEditor } from "./ReceiveRollsEditor";

interface ReceiveLineEditorProps {
  ctx: ReceivingLineOut;
  draft: ReceiptLineDraft;
  analysis: LineAnalysis | undefined;
  onChange: (next: ReceiptLineDraft) => void;
  locationOptions: SelectOption[];
  /** True once the operator has tried to post: unfinished-set errors show only from then on. */
  attempted: boolean;
  server: ServerLineProblems | undefined;
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="tnum text-sm font-semibold text-ink">{value}</dd>
    </div>
  );
}

/**
 * One purchase order line in the receiving form: what the order expects, then what arrived. A line
 * left with no quantity is not part of the receipt. The figures and the tolerance ceiling come from
 * the receiving context — advisory, since the server re-checks them under the order's lock.
 */
export function ReceiveLineEditor({
  ctx,
  draft,
  analysis,
  onChange,
  locationOptions,
  attempted,
  server,
}: ReceiveLineEditorProps) {
  const titleId = useId();
  const uom = ctx.stock_uom ?? "";
  const set = (patch: Partial<ReceiptLineDraft>) => onChange({ ...draft, ...patch });

  const maxMilliText = `${formatQty(ctx.max_receivable_qty)} ${uom}`.trim();
  const closed = ctx.short_closed;
  const nothingLeft = !closed && qtyToMilli(ctx.max_receivable_qty) === 0;
  const receivable = !closed && !nothingLeft;

  const errors = analysis?.errors;
  const quantityError = errors?.quantity ?? server?.quantity;
  const quantity = draft.quantity.trim();
  const receiving = analysis !== undefined && analysis.included && analysis.stockMilli !== null && analysis.stockMilli > 0;
  const hold = effectiveHold(draft, ctx);
  const unitOptions: SelectOption[] = [
    { value: "", label: uom },
    ...ctx.conversions.map((c) => ({ value: c.uom, label: `${c.uom} (1 = ${c.numerator}${c.denominator === 1 ? "" : `/${c.denominator}`} ${uom})` })),
  ];

  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-4 rounded-tile border border-line p-4">
      <div className="flex flex-col leading-tight">
        <h3 id={titleId} className="text-card-title font-semibold text-ink">
          {ctx.description || ctx.sku}
        </h3>
        <span className="text-xs text-faint tnum">{ctx.sku}</span>
      </div>

      <dl className="grid grid-cols-1 gap-3 rounded-tile bg-ground p-3 min-[400px]:grid-cols-2 sm:grid-cols-4">
        <Figure label="Ordered" value={`${formatQty(ctx.ordered_qty)} ${uom}`.trim()} />
        <Figure label="Already received" value={`${formatQty(ctx.received_qty)} ${uom}`.trim()} />
        <Figure label="Remaining" value={`${formatQty(ctx.remaining_qty)} ${uom}`.trim()} />
        <Figure label={`Most you can receive (${ctx.tolerance_pct}% tolerance)`} value={maxMilliText} />
      </dl>

      {closed ? (
        <p className="text-sm text-muted">This line was closed short. Nothing more is expected on it.</p>
      ) : null}
      {nothingLeft ? <p className="text-sm text-muted">This line has been received in full.</p> : null}

      {ctx.inventory_item_id === null && receivable ? (
        <p className="text-sm text-muted">
          This is a service or logistics line: it has no stock item, so receiving it posts no stock.
        </p>
      ) : null}

      {receivable ? (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <TextField
              label="Quantity received"
              inputMode="decimal"
              value={draft.quantity}
              onChange={(e) => set({ quantity: e.target.value })}
              helper={`Leave blank to skip this line. In ${draft.uom !== "" ? draft.uom : uom}.`}
              {...(quantityError !== undefined ? { error: quantityError } : {})}
            />
            {ctx.conversions.length > 0 ? (
              <Select
                label="Unit"
                value={draft.uom}
                onChange={(e) => set({ uom: e.target.value })}
                options={unitOptions}
              />
            ) : null}
            <Select
              label="Location"
              value={draft.locationId}
              onChange={(e) => set({ locationId: e.target.value })}
              placeholder={`Default: ${ctx.default_location_label ?? "the item's home bin"}`}
              options={locationOptions}
            />
          </div>

          <div aria-live="polite" className="empty:hidden">
            {analysis?.hints.map((hint) => (
              <p key={hint} className="text-sm text-late">
                {hint}
              </p>
            ))}
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Switch
              label="Close short"
              checked={draft.closeShort}
              onChange={(checked) => set({ closeShort: checked })}
              description={
                draft.closeShort
                  ? "Nothing more is expected on this line after this receipt."
                  : "Whatever is still outstanding stays expected."
              }
            />
            <Switch
              label="Hold for quality"
              checked={hold}
              disabled={ctx.track_quality}
              onChange={(checked) => set({ hold: checked })}
              description={
                ctx.track_quality
                  ? "This item is always received into quality hold, then released after inspection."
                  : hold
                    ? "Received into quality hold: not available until it is released."
                    : "Available as soon as it is received."
              }
            />
          </div>

          {(ctx.track_lot || ctx.track_expiry) && quantity !== "" && (analysis?.stockMilli ?? 0) > 0 ? (
            <fieldset className="flex flex-col gap-3 rounded-tile border border-line p-4">
              <legend className="px-1 text-sm font-bold text-ink">Lot</legend>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <TextField
                  label="Lot number"
                  value={draft.lotNo}
                  onChange={(e) => set({ lotNo: e.target.value })}
                  optional
                  maxLength={60}
                  helper="Blank generates one."
                  {...(errors?.lotNo !== undefined ? { error: errors.lotNo } : {})}
                />
                <TextField
                  label="Supplier lot number"
                  value={draft.supplierLotNo}
                  onChange={(e) => set({ supplierLotNo: e.target.value })}
                  optional
                  maxLength={60}
                />
                <TextField
                  label="Manufactured on"
                  type="date"
                  value={draft.manufacturedOn}
                  onChange={(e) => set({ manufacturedOn: e.target.value })}
                  optional
                  {...(errors?.manufacturedOn !== undefined ? { error: errors.manufacturedOn } : {})}
                />
                <TextField
                  label="Expires on"
                  type="date"
                  value={draft.expiresOn}
                  onChange={(e) => set({ expiresOn: e.target.value })}
                  required={ctx.track_expiry}
                  optional={!ctx.track_expiry}
                  {...(attempted && errors?.expiresOn !== undefined ? { error: errors.expiresOn } : {})}
                />
              </div>
            </fieldset>
          ) : null}

          {ctx.track_roll && receiving && analysis ? (
            <ReceiveRollsEditor
              rolls={draft.rolls}
              onChange={(rolls) => set({ rolls })}
              stockUom={uom}
              targetMilli={analysis.stockMilli}
              rollsMilli={analysis.rollsMilli}
              errors={analysis.errors}
              showErrors={attempted}
            />
          ) : null}

          {server && server.other.length > 0 ? (
            <InlineAlert tone="danger" title={server.other.join(" ")} />
          ) : null}
        </>
      ) : null}
    </section>
  );
}
