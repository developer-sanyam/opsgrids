import { useState } from "react";

import { issueProductionMaterials } from "@/api/client";
import type { IssueIn, IssueLineIn, ProductionActionOut, ProductionOrderDetailOut } from "@/api/types";
import { useLocationOptions } from "@/data/useLocationOptions";
import { useOrderAction } from "@/forms/production/useOrderAction";
import { qtyFieldError } from "@/lib/quantity";
import { formDialogError } from "@/lib/formError";
import { Button, CheckboxGroup, FormDialog, RadioGroup, Select, TextField } from "@/ui";

interface IssueDialogProps {
  order: ProductionOrderDetailOut;
  onClose: () => void;
  onDone: (result: ProductionActionOut) => void;
}

interface LineDraft {
  key: number;
  materialId: string;
  locationId: string;
  lotId: string;
  rollId: string;
  quantity: string;
}

type Mode = "reserved" | "lines";

const OVER_ISSUE = "over_issue";

function blankLine(key: number): LineDraft {
  return { key, materialId: "", locationId: "", lotId: "", rollId: "", quantity: "" };
}

/**
 * `POST .../orders/{id}/issue` — `...production_order.issue`. The default issues every open
 * reservation in full. The advanced mode names material, location and (optionally) lot and roll
 * per line, for stock that was never reserved or that should come from a particular place.
 * "Allow over-issue" lets a line go past a material's requirement; the excess shows as variance.
 * The server's 409 (insufficient stock, nothing reserved) is shown as the dialog's message.
 */
export function IssueDialog({ order, onClose, onDone }: IssueDialogProps) {
  const [mode, setMode] = useState<Mode>("reserved");
  const [lines, setLines] = useState<LineDraft[]>([blankLine(1)]);
  const [nextKey, setNextKey] = useState(2);
  const [overIssue, setOverIssue] = useState(false);
  const [lineErrors, setLineErrors] = useState<Record<number, Record<string, string>>>({});
  const locations = useLocationOptions();

  const issue = useOrderAction<IssueIn>((input, options) => issueProductionMaterials(order.id, input, options), onDone);

  function patchLine(key: number, patch: Partial<LineDraft>) {
    setLines((current) => current.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  }

  function submit() {
    if (mode === "reserved") {
      void issue.mutate({ over_issue: overIssue });
      return;
    }
    const errors: Record<number, Record<string, string>> = {};
    for (const line of lines) {
      const e: Record<string, string> = {};
      if (line.materialId === "") e.material = "Choose a material.";
      if (line.locationId === "") e.location = "Choose where to issue from.";
      const qtyError = qtyFieldError(line.quantity);
      if (qtyError) e.quantity = qtyError;
      if (Object.keys(e).length > 0) errors[line.key] = e;
    }
    setLineErrors(errors);
    if (Object.keys(errors).length > 0) return;
    const body: IssueLineIn[] = lines.map((l) => ({
      material_id: l.materialId,
      location_id: l.locationId,
      ...(l.lotId.trim() !== "" ? { lot_id: l.lotId.trim() } : {}),
      ...(l.rollId.trim() !== "" ? { roll_id: l.rollId.trim() } : {}),
      quantity: l.quantity.trim(),
    }));
    void issue.mutate({ lines: body, over_issue: overIssue });
  }

  const materialOptions = order.materials.map((m) => ({ value: m.id, label: `${m.sku} — ${m.name}` }));

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Issue material for ${order.number}`}
      description="Moves material from stock to work in progress."
      submitLabel="Issue material"
      pendingLabel="Issuing…"
      pending={issue.pending}
      size="lg"
      dirty={mode !== "reserved" || overIssue || lines.some((l) => l.materialId !== "" || l.quantity !== "")}
      {...formDialogError(issue.error, issue.fieldErrors)}
    >
      <RadioGroup
        legend="What to issue"
        name="issue-mode"
        value={mode}
        onChange={(v) => setMode(v as Mode)}
        options={[
          { value: "reserved", label: "Everything reserved", description: "Issues every open reservation in full." },
          { value: "lines", label: "Choose lines", description: "Name the material, location and quantity yourself." },
        ]}
      />

      {mode === "lines" ? (
        <div className="flex flex-col gap-4">
          {lines.map((line, index) => {
            const e = lineErrors[line.key] ?? {};
            return (
              <fieldset key={line.key} className="flex flex-col gap-3 rounded-tile border border-line p-4">
                <legend className="px-1 text-sm font-semibold text-ink">Line {index + 1}</legend>
                <Select
                  label="Material"
                  value={line.materialId}
                  onChange={(ev) => patchLine(line.key, { materialId: ev.target.value })}
                  options={materialOptions}
                  placeholder="Choose a material"
                  required
                  error={e.material}
                />
                <Select
                  label="Location"
                  value={line.locationId}
                  onChange={(ev) => patchLine(line.key, { locationId: ev.target.value })}
                  options={locations.options}
                  placeholder={locations.status === "loading" ? "Loading locations…" : "Choose a location"}
                  disabled={locations.status !== "success"}
                  required
                  error={e.location}
                />
                <TextField
                  label="Quantity"
                  inputMode="decimal"
                  value={line.quantity}
                  onChange={(ev) => patchLine(line.key, { quantity: ev.target.value })}
                  required
                  error={e.quantity}
                />
                <TextField
                  label="Lot id"
                  value={line.lotId}
                  onChange={(ev) => patchLine(line.key, { lotId: ev.target.value })}
                  optional
                />
                <TextField
                  label="Roll id"
                  value={line.rollId}
                  onChange={(ev) => patchLine(line.key, { rollId: ev.target.value })}
                  optional
                />
                {lines.length > 1 ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="self-start"
                    onClick={() => setLines((current) => current.filter((l) => l.key !== line.key))}
                  >
                    {`Remove line ${index + 1}`}
                  </Button>
                ) : null}
              </fieldset>
            );
          })}
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="self-start"
            onClick={() => {
              setLines((current) => [...current, blankLine(nextKey)]);
              setNextKey((k) => k + 1);
            }}
          >
            Add line
          </Button>
        </div>
      ) : null}

      <CheckboxGroup
        label="Options"
        options={[
          {
            value: OVER_ISSUE,
            label: "Allow over-issue",
            description: "Lets a line go past what the order needs. The excess shows up as variance.",
          },
        ]}
        value={overIssue ? [OVER_ISSUE] : []}
        onChange={(next) => setOverIssue(next.includes(OVER_ISSUE))}
      />
    </FormDialog>
  );
}
