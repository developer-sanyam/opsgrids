import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import { Button, Card, ErrorState, InlineAlert, PageHeader, RadioGroup, Skeleton, TextArea, TextField } from "@opsgrids/ui";
import type { RadioOption } from "@opsgrids/ui";

import { confirmPick, getTask } from "@/api/client";
import type { PickExceptionReason } from "@/api/types";
import { RequireGrant } from "@/auth/RequireGrant";
import { formatQty, milliToQty, qtyToMilli } from "@/lib/quantity";
import { itemTitle, orderLabel, qtyWithUnit, sourceLabel } from "@/lib/display";
import { StickyAction } from "@/ui/StickyAction";
import { remainingMilli } from "@/worker/taskLines";

const REASON_OPTIONS: RadioOption[] = [
  { value: "short_pick", label: "Short pick", description: "Less was available at the location than expected." },
  { value: "damaged", label: "Damaged", description: "Stock was there but not sellable." },
  { value: "not_found", label: "Not found", description: "Couldn't locate the item at all." },
  { value: "other", label: "Other" },
];

export function PickConfirmPage() {
  return (
    <RequireGrant permission="operations.fulfilment.task.execute" what="confirming a pick">
      <PickConfirmContent />
    </RequireGrant>
  );
}

function PickConfirmContent() {
  const { taskId, lineId } = useParams<{ taskId: string; lineId: string }>();
  const navigate = useNavigate();
  const state = useAsync(() => getTask(taskId ?? ""), [taskId]);

  if (!taskId || !lineId) return null;

  if (state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <Skeleton className="h-9 w-40 rounded-tile" />
        <Skeleton className="h-72 w-full rounded-tile" />
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader title="Confirm pick" />
        <Card className="p-2">
          <ErrorState title="Couldn't load this task" message={problemMessage(state.error)} onRetry={state.reload} />
        </Card>
      </div>
    );
  }

  const task = state.data;
  const line = task.lines.find((candidate) => candidate.id === lineId);

  if (!line) {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader title="Confirm pick" />
        <Card className="p-2">
          <ErrorState
            title="That line isn't on this task"
            message="It may have moved, or the link is out of date."
            action={
              <Link to={`/tasks/${taskId}`} className="text-sm font-semibold text-accent hover:underline">
                Back to task
              </Link>
            }
          />
        </Card>
      </div>
    );
  }

  const remaining = remainingMilli(line);

  if (remaining <= 0) {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader title="Confirm pick" />
        <InlineAlert tone="info" title="This line has already been confirmed">
          <Link to={`/tasks/${taskId}`} className="font-semibold underline">
            Back to task
          </Link>
        </InlineAlert>
      </div>
    );
  }

  if (task.status !== "ACCEPTED" && task.status !== "IN_PROGRESS") {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader title="Confirm pick" />
        <InlineAlert tone="warning" title={`This task is ${task.status.toLowerCase().replace("_", " ")}`}>
          A line can only be confirmed while the task is accepted or in progress.
          <Link to={`/tasks/${taskId}`} className="ml-1 font-semibold underline">
            Back to task
          </Link>
        </InlineAlert>
      </div>
    );
  }

  return (
    <PickForm
      taskId={taskId}
      lineId={lineId}
      title={itemTitle(line)}
      sku={line.item_name ? line.sku : null}
      unit={line.item_unit}
      orderNumber={orderLabel(task)}
      source={sourceLabel(line)}
      remainingMilli={remaining}
      onDone={() => navigate(`/tasks/${taskId}`)}
    />
  );
}

function PickForm({
  taskId,
  lineId,
  title,
  sku,
  unit,
  orderNumber,
  source,
  remainingMilli: remaining,
  onDone,
}: {
  taskId: string;
  lineId: string;
  title: string;
  /** Shown under the title when the title is the item's name rather than the SKU itself. */
  sku: string | null;
  unit: string | null;
  orderNumber: string;
  source: string;
  remainingMilli: number;
  onDone: () => void;
}) {
  const [quantity, setQuantity] = useState(milliToQty(remaining));
  const [reason, setReason] = useState<PickExceptionReason | null>(null);
  const [note, setNote] = useState("");
  const [quantityError, setQuantityError] = useState<string | undefined>(undefined);
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);

  const confirm = useMutation(
    (input: { quantity: string; reason: PickExceptionReason | null; note: string }) =>
      confirmPick(taskId, lineId, {
        quantity: input.quantity,
        ...(input.reason ? { reason: input.reason } : {}),
        note: input.note,
      }),
    { onSuccess: onDone },
  );

  function submit() {
    setQuantityError(undefined);
    setReasonError(undefined);

    let parsed: number;
    try {
      parsed = qtyToMilli(quantity.trim());
    } catch {
      setQuantityError("Enter a quantity like 12.5.");
      return;
    }
    if (parsed <= 0) {
      setQuantityError("Enter a quantity greater than zero.");
      return;
    }
    if (parsed > remaining) {
      setQuantityError(`Only ${formatQty(milliToQty(remaining))} remains on this line.`);
      return;
    }
    const isShort = parsed < remaining;
    if (isShort && reason === null) {
      setReasonError("Say why the rest wasn't picked.");
      return;
    }
    void confirm.mutate({ quantity: quantity.trim(), reason: isShort ? reason : null, note: note.trim() });
  }

  const isShortEntry = (() => {
    try {
      return qtyToMilli(quantity.trim()) < remaining;
    } catch {
      return false;
    }
  })();

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader title="Confirm pick" description={`Order ${orderNumber} · from ${source}`} />

      <Card className="flex flex-col gap-4 p-4">
        <div className="flex flex-col gap-1">
          <span className="text-base font-semibold text-ink">{title}</span>
          {sku ? <span className="font-mono text-xs text-muted">{sku}</span> : null}
          <span className="text-sm text-muted">{qtyWithUnit(milliToQty(remaining), unit)} remaining on this line</span>
        </div>

        {confirm.error ? (
          <InlineAlert
            tone="danger"
            title={
              confirm.error instanceof ApiError && confirm.error.status === 409
                ? "This line changed since you opened it"
                : "Couldn't confirm this pick"
            }
          >
            {problemMessage(confirm.error)}
          </InlineAlert>
        ) : null}

        <TextField
          label="Quantity picked"
          inputMode="decimal"
          value={quantity}
          onChange={(event) => setQuantity(event.target.value)}
          {...(quantityError !== undefined ? { error: quantityError } : {})}
        />

        {isShortEntry ? (
          <RadioGroup
            legend="Why is the rest short?"
            name="pick-exception-reason"
            value={reason}
            onChange={(value) => setReason(value as PickExceptionReason)}
            options={REASON_OPTIONS}
            {...(reasonError !== undefined ? { error: reasonError } : {})}
          />
        ) : null}

        <TextArea
          label="Note"
          optional
          value={note}
          onChange={(event) => setNote(event.target.value)}
          maxLength={500}
        />

      </Card>

      <StickyAction>
        <Button className="min-h-12 w-full" onClick={submit} loading={confirm.pending} disabled={confirm.pending}>
          {isShortEntry ? "Confirm short pick" : "Confirm pick"}
        </Button>
      </StickyAction>
    </div>
  );
}
