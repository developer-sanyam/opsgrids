import { useRef, useState } from "react";

import { createMrpRun, getMrpDemandSources } from "@/api/client";
import type { MrpRunCreatedOut } from "@/api/types";
import { OrderItemPicker } from "@/forms/OrderItemPicker";
import { formDialogError } from "@/lib/formError";
import { EMPTY_FORM, buildMrpRun, lineErrorKey, newLine, whereItRuns } from "@/lib/mrpForm";
import type { ManualLineState, MrpFormState } from "@/lib/mrpForm";
import { useIdempotencyKeys } from "@/lib/useIdempotencyKeys";
import { useAsync, useMutation } from "@opsgrids/client";
import { Button, CheckboxGroup, FormDialog, InlineAlert, Switch, TextArea, TextField } from "@/ui";
import type { ComboboxOption } from "@/ui";
import { CloseIcon, PlusIcon } from "@opsgrids/ui";

interface MrpRunFormDialogProps {
  onClose: () => void;
  onStarted: (run: MrpRunCreatedOut) => void;
}

let lineCounter = 0;
function nextLine(): ManualLineState {
  lineCounter += 1;
  return newLine(`l${lineCounter}`);
}

const SOURCE_OPTIONS = [
  { value: "sales", label: "Open sales orders", description: "Placed, accepted or in fulfilment, less what they already reserved." },
  { value: "production", label: "Open production orders", description: "The material they still need, less what is already reserved or issued." },
  { value: "manual", label: "Manual demand", description: "Items and quantities you enter below." },
];

/**
 * Start an MRP run (`POST .../mrp/runs`, `operations.manufacturing.mrp.run`). The demand sources,
 * any manual lines, the date effective dates are read on, and whether to force the background
 * worker. Quantities travel as typed text; the same body gets the same `Idempotency-Key`, so a
 * double click or a retry replays the first run instead of starting a second.
 */
export function MrpRunFormDialog({ onClose, onStarted }: MrpRunFormDialogProps) {
  const [state, setState] = useState<MrpFormState>(EMPTY_FORM);
  const [items, setItems] = useState<Record<string, ComboboxOption | null>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const firstRef = useRef<HTMLInputElement>(null);
  const keyFor = useIdempotencyKeys();
  const preview = useAsync(() => getMrpDemandSources(), []);

  const patch = (next: Partial<MrpFormState>) => setState((s) => ({ ...s, ...next }));
  const setLine = (key: string, next: Partial<ManualLineState>) =>
    setState((s) => ({ ...s, lines: s.lines.map((l) => (l.key === key ? { ...l, ...next } : l)) }));

  const start = useMutation((input: Parameters<typeof createMrpRun>[0]) =>
    createMrpRun(input, { idempotencyKey: keyFor(input) }),
    { onSuccess: (run) => onStarted(run) },
  );

  const dirty =
    state.manual ||
    !state.sales ||
    !state.production ||
    state.lines.length > 0 ||
    state.planDate !== "" ||
    state.notes !== "" ||
    state.background;

  function submit() {
    const result = buildMrpRun(state);
    setErrors(result.errors);
    if (result.input === null) return;
    void start.mutate(result.input);
  }

  const selected = [
    ...(state.sales ? ["sales"] : []),
    ...(state.production ? ["production"] : []),
    ...(state.manual ? ["manual"] : []),
  ];
  const fieldError = (field: string) => errors[field] ?? start.fieldErrors[field];
  const where = whereItRuns(state, preview.status === "success" ? preview.data : null);

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="New MRP run"
      description="Explodes demand through the bills of materials and nets it against stock, reservations, open purchase orders and open production."
      submitLabel="Run planning"
      pendingLabel="Starting…"
      pending={start.pending}
      initialFocusRef={firstRef}
      dirty={dirty}
      size="lg"
      {...formDialogError(start.error, start.fieldErrors)}
    >
      <CheckboxGroup
        label="Plan from"
        options={SOURCE_OPTIONS}
        value={selected}
        onChange={(next) => {
          const manual = next.includes("manual");
          patch({
            sales: next.includes("sales"),
            production: next.includes("production"),
            manual,
            lines: manual && state.lines.length === 0 ? [nextLine()] : state.lines,
          });
        }}
        error={fieldError("sources")}
      />

      {preview.status === "success" ? (
        <p className="text-sm text-muted tnum">
          Open now: {preview.data.sales_order_lines} sales order{" "}
          {preview.data.sales_order_lines === 1 ? "line" : "lines"},{" "}
          {preview.data.production_order_materials} production{" "}
          {preview.data.production_order_materials === 1 ? "material" : "materials"}.
        </p>
      ) : null}

      {state.manual ? (
        <fieldset className="flex flex-col gap-4">
          <legend className="text-sm font-semibold text-ink">Manual demand</legend>
          {state.lines.map((line, index) => (
            <div key={line.key} className="flex flex-col gap-3 rounded-tile border border-hairline p-3">
              <OrderItemPicker
                label={`Item ${index + 1}`}
                value={items[line.key] ?? null}
                onChange={(option) => {
                  setItems((prev) => ({ ...prev, [line.key]: option }));
                  setLine(line.key, { itemId: option?.id ?? null });
                }}
                error={fieldError(lineErrorKey(line, "item"))}
              />
              <div className="grid gap-3 sm:grid-cols-2">
                <TextField
                  ref={index === 0 ? firstRef : undefined}
                  label="Quantity"
                  inputMode="decimal"
                  value={line.quantity}
                  onChange={(e) => setLine(line.key, { quantity: e.target.value })}
                  required
                  error={fieldError(lineErrorKey(line, "quantity"))}
                />
                <TextField
                  label="Needed by"
                  type="date"
                  value={line.dueDate}
                  onChange={(e) => setLine(line.key, { dueDate: e.target.value })}
                  optional
                />
              </div>
              <TextField
                label="Note"
                value={line.note}
                onChange={(e) => setLine(line.key, { note: e.target.value })}
                optional
              />
              <div>
                <Button
                  variant="ghost"
                  size="sm"
                  leadingIcon={<CloseIcon className="size-4" />}
                  onClick={() => patch({ lines: state.lines.filter((l) => l.key !== line.key) })}
                >
                  Remove line {index + 1}
                </Button>
              </div>
            </div>
          ))}
          <div>
            <Button
              variant="secondary"
              size="sm"
              leadingIcon={<PlusIcon className="size-4" />}
              onClick={() => patch({ lines: [...state.lines, nextLine()] })}
            >
              Add a line
            </Button>
          </div>
        </fieldset>
      ) : null}

      <TextField
        ref={state.manual ? undefined : firstRef}
        label="Plan date"
        type="date"
        value={state.planDate}
        onChange={(e) => patch({ planDate: e.target.value })}
        helper="Which bill-of-materials version and line windows apply. Today if blank."
        optional
      />
      <TextArea
        label="Notes"
        value={state.notes}
        onChange={(e) => patch({ notes: e.target.value })}
        optional
      />
      <Switch
        label="Run in the background"
        description="Queue it for the worker even if it is small. Large runs are queued automatically."
        checked={state.background}
        onChange={(checked) => patch({ background: checked })}
      />
      {where ? <InlineAlert tone="info" title={where} /> : null}
    </FormDialog>
  );
}
