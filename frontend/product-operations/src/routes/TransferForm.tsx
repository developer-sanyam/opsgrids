import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getInventoryItem } from "@/api/client";
import { createTransfer, getTransfer, updateTransfer } from "@/api/movements";
import type { TransferIn, TransferOut } from "@/api/movementTypes";
import { Can, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import { useWarehouseIdOptions } from "@/data/useWarehouseOptions";
import { formDialogError } from "@/lib/formError";
import { lineFieldErrors } from "@/lib/lineErrors";
import { blankTransferLine, validateTransferForm } from "@/lib/transferForm";
import type { TransferFormErrors } from "@/lib/transferForm";
import { useSubmissionKeys } from "@/lib/useSubmissionKeys";
import { errorProp } from "@/movements/errorProp";
import { TransferLineEditor } from "@/movements/TransferLineEditor";
import type { TransferLineState } from "@/movements/TransferLineEditor";
import { useDiscreteUoms, useLocationChoices } from "@/movements/useMovementReference";
import {
  Button,
  Card,
  ErrorState,
  FormSection,
  InlineAlert,
  PageHeader,
  Select,
  Skeleton,
  TextArea,
  TextField,
  UnsavedChangesGuard,
} from "@/ui";
import { ArrowRightIcon, PlusIcon, useToast } from "@opsgrids/ui";

const MAX_LINES = 100;

interface Initial {
  fromWarehouseId: string;
  toWarehouseId: string;
  lines: TransferLineState[];
  note: string;
  expectedOn: string;
}

function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      {label}
    </Link>
  );
}

let rowCounter = 0;
function nextKey(): string {
  rowCounter += 1;
  return `row-${rowCounter}`;
}

function blankLine(): TransferLineState {
  return { values: blankTransferLine(nextKey()), itemOption: null, tracksLot: false, tracksRoll: false };
}

interface BodyProps {
  /** The draft being edited, or `null` for a new transfer. */
  existing: TransferOut | null;
  initial: Initial;
}

function TransferFormBody({ existing, initial }: BodyProps) {
  const navigate = useNavigate();
  const toast = useToast();
  const warehouses = useWarehouseIdOptions();
  const discrete = useDiscreteUoms();
  const keyFor = useSubmissionKeys();

  const [fromWarehouseId, setFromWarehouseId] = useState(initial.fromWarehouseId);
  const [toWarehouseId, setToWarehouseId] = useState(initial.toWarehouseId);
  const [lines, setLines] = useState<TransferLineState[]>(initial.lines);
  const [note, setNote] = useState(initial.note);
  const [expectedOn, setExpectedOn] = useState(initial.expectedOn);
  const [errors, setErrors] = useState<TransferFormErrors>({});
  const [savedId, setSavedId] = useState<string | null>(null);

  const bins = useLocationChoices(fromWarehouseId);
  const baseline = useRef(JSON.stringify(snapshot(initial.fromWarehouseId, initial.toWarehouseId, initial.lines, initial.note, initial.expectedOn)));
  const dirty =
    savedId === null &&
    JSON.stringify(snapshot(fromWarehouseId, toWarehouseId, lines, note, expectedOn)) !== baseline.current;

  const save = useMutation(
    (input: TransferIn) =>
      existing === null
        ? createTransfer(input, keyFor(input))
        : updateTransfer(existing.id, {
            to_warehouse_id: input.to_warehouse_id,
            lines: input.lines,
            ...(input.note !== undefined ? { note: input.note } : {}),
            expected_on: input.expected_on ?? null,
          }),
    {
      onSuccess: (saved) => {
        toast.success(existing === null ? `${saved.number} saved as a draft.` : `${saved.number} updated.`);
        setSavedId(saved.id);
      },
    },
  );

  const field = save.fieldErrors;
  const failure = formDialogError(save.error, field);

  // Leave only once the guard has seen the form stop being dirty, or it asks about saved changes.
  useEffect(() => {
    if (savedId !== null) navigate(`/inventory/transfers/${savedId}`);
  }, [savedId, navigate]);

  function updateLine(key: string, update: (previous: TransferLineState) => TransferLineState) {
    setLines((previous) => previous.map((l) => (l.values.key === key ? update(l) : l)));
  }

  function changeSource(next: string) {
    setFromWarehouseId(next);
    // A bin belongs to one warehouse; the old choices no longer apply.
    setLines((previous) => previous.map((l) => ({ ...l, values: { ...l.values, fromLocationId: "" } })));
  }

  function submit() {
    const result = validateTransferForm(
      { fromWarehouseId, toWarehouseId, lines: lines.map((l) => l.values), note, expectedOn },
      discrete,
    );
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    setErrors({});
    void save.mutate(result.payload);
  }

  const title = existing === null ? "New transfer" : `Edit ${existing.number}`;
  const toOptions = warehouses.options.filter((w) => w.value !== fromWarehouseId);

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink
        to={existing === null ? "/inventory/transfers" : `/inventory/transfers/${existing.id}`}
        label={existing === null ? "Back to transfers" : `Back to ${existing.number}`}
      />
      <PageHeader
        eyebrow="Inventory"
        title={title}
        description="Move stock from one warehouse to another. It leaves the source when you dispatch, and counts as in transit until the destination receives it."
      />
      <UnsavedChangesGuard when={dirty} subject="this transfer" />

      <form noValidate onSubmit={(e) => { e.preventDefault(); submit(); }} className="flex flex-col gap-6">
        <Card className="p-4 sm:p-6">
          <FormSection title="Warehouses" description="Where the stock leaves, and where it is going." headingLevel={2}>
            {existing === null ? (
              <Select
                label="From warehouse"
                value={fromWarehouseId}
                onChange={(e) => changeSource(e.target.value)}
                options={warehouses.options}
                placeholder={warehouses.status === "loading" ? "Loading warehouses…" : "Choose a warehouse"}
                disabled={warehouses.status === "loading"}
                required
                {...errorProp(errors.fromWarehouse, field.from_warehouse_id)}
              />
            ) : (
              <TextField
                label="From warehouse"
                value={existing.from_warehouse.name}
                readOnly
                helper="The source can't change once a transfer is drafted."
              />
            )}
            <Select
              label="To warehouse"
              value={toWarehouseId}
              onChange={(e) => setToWarehouseId(e.target.value)}
              options={toOptions}
              placeholder={warehouses.status === "loading" ? "Loading warehouses…" : "Choose a warehouse"}
              disabled={warehouses.status === "loading"}
              required
              {...errorProp(errors.toWarehouse, field.to_warehouse_id)}
            />
            <TextField
              label="Expected arrival"
              type="date"
              value={expectedOn}
              onChange={(e) => setExpectedOn(e.target.value)}
              optional
              {...errorProp(field.expected_on)}
            />
          </FormSection>
        </Card>

        <Card className="p-4 sm:p-6 flex flex-col gap-4">
          <FormSection title="Lines" description="What is moving, from which bin, and how much." headingLevel={2}>
            <div className="col-span-full flex flex-col gap-4">
              {errors.lines !== undefined ? (
                <p role="alert" className="text-sm text-negative">
                  {errors.lines}
                </p>
              ) : null}
              {lines.map((line, index) => (
                <TransferLineEditor
                  key={line.values.key}
                  index={index}
                  line={line}
                  binOptions={bins.options}
                  binsLoading={bins.status === "loading"}
                  sourceChosen={fromWarehouseId !== ""}
                  canRemove={lines.length > 1}
                  errors={errors.lineErrors?.[line.values.key] ?? {}}
                  serverErrors={lineFieldErrors(field, "lines", index)}
                  onChange={(update) => updateLine(line.values.key, update)}
                  onRemove={() => setLines((previous) => previous.filter((l) => l.values.key !== line.values.key))}
                />
              ))}
              <div>
                <Button
                  type="button"
                  variant="secondary"
                  leadingIcon={<PlusIcon className="size-4" />}
                  disabled={lines.length >= MAX_LINES}
                  onClick={() => setLines((previous) => [...previous, blankLine()])}
                >
                  Add line
                </Button>
              </div>
            </div>
          </FormSection>
        </Card>

        <Card className="p-4 sm:p-6">
          <FormSection title="Notes" headingLevel={2}>
            <div className="col-span-full">
              <TextArea
                label="Note"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                optional
                maxLength={500}
                counterFrom={400}
                {...errorProp(field.note)}
              />
            </div>
          </FormSection>
        </Card>

        {failure.error !== undefined ? (
          <InlineAlert
            tone="danger"
            title={failure.error}
            {...(failure.reference !== undefined ? { reference: failure.reference } : {})}
          />
        ) : null}

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button
            type="button"
            variant="ghost"
            onClick={() => navigate(existing === null ? "/inventory/transfers" : `/inventory/transfers/${existing.id}`)}
            disabled={save.pending}
          >
            Cancel
          </Button>
          <Button type="submit" loading={save.pending}>
            {save.pending ? "Saving…" : existing === null ? "Save draft" : "Save changes"}
          </Button>
        </div>
      </form>
    </div>
  );
}

function snapshot(from: string, to: string, lines: TransferLineState[], note: string, expectedOn: string) {
  return { from, to, lines: lines.map((l) => l.values), note, expectedOn };
}

/**
 * `/inventory/transfers/new` and `/inventory/transfers/:id/edit` — a DRAFT transfer between two
 * warehouses (`operations.inventory.transfer.write`). Saving makes or updates the draft; requesting
 * approval, approving, dispatching and receiving happen on the detail screen.
 */
function TransferFormRoute() {
  const { id } = useParams();
  const editing = id !== undefined;

  const state = useAsync(async () => {
    if (id === undefined) return null;
    const transfer = await getTransfer(id);
    // Tracked items ask for lot/roll ids; the line itself only knows the ids, so read the item.
    const items = await Promise.allSettled(transfer.lines.map((l) => getInventoryItem(l.inventory_item_id)));
    return { transfer, items };
  }, [id]);

  const content = (() => {
    if (!editing) {
      return (
        <TransferFormBody
          existing={null}
          initial={{ fromWarehouseId: "", toWarehouseId: "", lines: [blankLine()], note: "", expectedOn: "" }}
        />
      );
    }
    if (state.status === "loading") {
      return (
        <div className="rise flex flex-col gap-6" aria-busy="true">
          <Skeleton className="h-8 w-40 rounded-tile" />
          <Card className="p-6 flex flex-col gap-4">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-40 w-full rounded-tile" />
          </Card>
        </div>
      );
    }
    if (state.status === "error" || state.data === null) {
      return (
        <div className="rise flex flex-col gap-6">
          <BackLink to="/inventory/transfers" label="Back to transfers" />
          <Card className="p-6">
            <ErrorState
              title="Couldn't load this transfer"
              message={state.status === "error" ? problemMessage(state.error) : "That transfer could not be found."}
              {...(state.status === "error" ? { onRetry: state.reload } : {})}
            />
          </Card>
        </div>
      );
    }
    const { transfer, items } = state.data;
    if (transfer.status !== "DRAFT") {
      return (
        <div className="rise flex flex-col gap-6">
          <BackLink to={`/inventory/transfers/${transfer.id}`} label={`Back to ${transfer.number}`} />
          <InlineAlert tone="warning" title={`${transfer.number} can no longer be edited`}>
            Only a draft can be edited, and this transfer is {transfer.status.toLowerCase().replace("_", " ")}.
          </InlineAlert>
        </div>
      );
    }
    const lines: TransferLineState[] = transfer.lines.map((l, index) => {
      const fetched = items[index];
      const item = fetched?.status === "fulfilled" ? fetched.value : null;
      return {
        values: {
          key: nextKey(),
          inventoryItemId: l.inventory_item_id,
          uom: l.uom,
          fromLocationId: l.from_location_id,
          quantity: l.quantity,
          lotId: l.lot_id ?? "",
          rollId: l.roll_id ?? "",
        },
        itemOption: { id: l.inventory_item_id, label: l.sku, sublabel: l.item_name },
        tracksLot: item?.track_lot ?? false,
        tracksRoll: item?.track_roll ?? false,
      };
    });
    return (
      <TransferFormBody
        existing={transfer}
        initial={{
          fromWarehouseId: transfer.from_warehouse.id,
          toWarehouseId: transfer.to_warehouse.id,
          lines,
          note: transfer.note,
          expectedOn: transfer.expected_on ?? "",
        }}
      />
    );
  })();

  return content;
}

export function TransferFormPage() {
  return (
    <Can
      permission="operations.inventory.transfer.write"
      scope="ORG"
      fallback={
        <InlineAlert tone="warning" title="You can't create or edit transfers">
          Ask an administrator for the transfer write permission.
        </InlineAlert>
      }
    >
      <TransferFormRoute />
    </Can>
  );
}
