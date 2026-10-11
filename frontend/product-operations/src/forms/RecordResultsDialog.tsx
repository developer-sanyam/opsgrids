import { useState } from "react";

import { getQcTemplate, recordInspectionResults } from "@/api/client";
import type { InspectionOut, InspectionResultIn, QcParamOut } from "@/api/types";
import { problemMessage, useAsync, useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { formatBounds, isDecimal } from "@/lib/quality";
import { Button, ErrorState, FormDialog, Select, Skeleton, TextField } from "@/ui";

interface RecordResultsDialogProps {
  inspection: InspectionOut;
  onClose: () => void;
  onSaved: (inspection: InspectionOut) => void;
}

interface Row {
  key: string;
  name: string;
  kind: "numeric" | "pass_fail";
  unit: string;
  bounds: string;
  /** Typed reading for a numeric row, kept as text — never parsed to a number. */
  value: string;
  /** `""` = not answered yet. */
  verdict: "" | "pass" | "fail";
  note: string;
  /** A template parameter's row is fixed; an ad-hoc observation can be removed. */
  fixed: boolean;
}

const VERDICT_OPTIONS = [
  { value: "pass", label: "Pass" },
  { value: "fail", label: "Fail" },
];

let counter = 0;
const nextKey = () => `row-${++counter}`;

function rowsFor(inspection: InspectionOut, params: QcParamOut[] | null): Row[] {
  const existing = new Map(inspection.results.map((r) => [r.param_name.toLowerCase(), r]));
  if (params) {
    return params.map((p) => {
      const r = existing.get(p.name.toLowerCase());
      return {
        key: nextKey(),
        name: p.name,
        kind: p.kind,
        unit: p.unit,
        bounds: formatBounds(p),
        value: r?.value ?? "",
        verdict: r ? (r.passed ? "pass" : "fail") : "",
        note: r?.note ?? "",
        fixed: true,
      };
    });
  }
  return inspection.results.map((r) => ({
    key: nextKey(),
    name: r.param_name,
    kind: "pass_fail" as const,
    unit: "",
    bounds: "Pass / fail",
    value: "",
    verdict: r.passed ? ("pass" as const) : ("fail" as const),
    note: r.note,
    fixed: false,
  }));
}

/**
 * `PUT /operations/quality/inspections/{id}/results` — record or correct readings while the
 * inspection is pending or on hold. A numeric reading is typed as text and sent as the decimal
 * string it is; the **server** decides pass or fail against the template's bounds, so the verdict
 * shown afterwards is never this form's guess.
 */
export function RecordResultsDialog({ inspection, onClose, onSaved }: RecordResultsDialogProps) {
  const template = useAsync(
    () => (inspection.template_id ? getQcTemplate(inspection.template_id) : Promise.resolve(null)),
    [inspection.template_id],
  );

  if (template.status === "loading") {
    return (
      <FormDialog open onClose={onClose} onSubmit={() => undefined} title="Record readings" submitLabel="Save readings" pending>
        <Skeleton className="h-24 w-full rounded-tile" />
      </FormDialog>
    );
  }
  if (template.status === "error") {
    return (
      <FormDialog open onClose={onClose} onSubmit={onClose} title="Record readings" submitLabel="Close">
        <ErrorState
          title="Couldn't load this inspection's template"
          message={problemMessage(template.error)}
          onRetry={template.reload}
        />
      </FormDialog>
    );
  }
  return (
    <RecordResultsForm
      inspection={inspection}
      params={template.data ? template.data.params : null}
      onClose={onClose}
      onSaved={onSaved}
    />
  );
}

function RecordResultsForm({
  inspection,
  params,
  onClose,
  onSaved,
}: RecordResultsDialogProps & { params: QcParamOut[] | null }) {
  const [rows, setRows] = useState<Row[]>(() => rowsFor(inspection, params));
  const [localError, setLocalError] = useState<string | undefined>(undefined);

  const save = useMutation(
    (results: InspectionResultIn[]) => recordInspectionResults(inspection.id, results),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function patch(key: string, change: Partial<Row>) {
    setRows((all) => all.map((r) => (r.key === key ? { ...r, ...change } : r)));
  }

  function submit() {
    setLocalError(undefined);
    const results: InspectionResultIn[] = [];
    for (const r of rows) {
      if (r.name.trim() === "") return setLocalError("Every observation needs a name.");
      if (r.kind === "numeric") {
        if (r.value.trim() === "") continue; // not measured yet — leave it out, not "0"
        if (!isDecimal(r.value)) {
          return setLocalError(`${r.name}: enter a number with at most three decimals.`);
        }
        results.push({ param_name: r.name.trim(), value: r.value.trim(), note: r.note });
      } else {
        if (r.verdict === "") continue;
        results.push({ param_name: r.name.trim(), passed: r.verdict === "pass", note: r.note });
      }
    }
    if (results.length === 0) return setLocalError("Record at least one reading.");
    void save.mutate(results);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Record readings"
      description={
        params
          ? "Enter what you measured. Pass or fail is worked out against the template's limits."
          : "This inspection has no template, so record pass / fail observations."
      }
      submitLabel="Save readings"
      pendingLabel="Saving…"
      pending={save.pending}
      size="lg"
      {...formDialogError(save.error, save.fieldErrors)}
    >
      {rows.length === 0 ? <p className="text-sm text-muted">No observations yet — add one below.</p> : null}
      {rows.map((r) => (
        <div key={r.key} className="rounded-tile border border-line p-3 flex flex-col gap-3">
          <div className="flex items-baseline justify-between gap-2">
            {r.fixed ? (
              <p className="font-semibold text-ink">{r.name}</p>
            ) : (
              <TextField
                label="Observation"
                value={r.name}
                onChange={(e) => patch(r.key, { name: e.target.value })}
                maxLength={80}
              />
            )}
            <span className="text-xs text-muted tnum">
              {r.bounds}
              {r.unit ? ` ${r.unit}` : ""}
            </span>
          </div>
          {r.kind === "numeric" ? (
            <TextField
              label={`${r.name || "Reading"} value`}
              value={r.value}
              onChange={(e) => patch(r.key, { value: e.target.value })}
              inputMode="decimal"
              placeholder="e.g. 182.5"
            />
          ) : (
            <Select
              label={`${r.name || "Observation"} result`}
              value={r.verdict}
              onChange={(e) => patch(r.key, { verdict: e.target.value as Row["verdict"] })}
              options={VERDICT_OPTIONS}
              placeholder="Not answered"
            />
          )}
          <TextField
            label={`${r.name || "Observation"} note`}
            value={r.note}
            onChange={(e) => patch(r.key, { note: e.target.value })}
            maxLength={500}
            optional
          />
          {!r.fixed ? (
            <div>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setRows((all) => all.filter((x) => x.key !== r.key))}
              >
                Remove
              </Button>
            </div>
          ) : null}
        </div>
      ))}
      {!params ? (
        <div>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() =>
              setRows((all) => [
                ...all,
                {
                  key: nextKey(),
                  name: "",
                  kind: "pass_fail",
                  unit: "",
                  bounds: "Pass / fail",
                  value: "",
                  verdict: "",
                  note: "",
                  fixed: false,
                },
              ])
            }
          >
            Add observation
          </Button>
        </div>
      ) : null}
      {localError ? (
        <p role="alert" className="text-sm text-danger">
          {localError}
        </p>
      ) : null}
    </FormDialog>
  );
}
