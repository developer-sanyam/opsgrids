import { useState } from "react";

import { createQcTemplate, updateQcTemplate } from "@/api/client";
import type { QcKind, QcTemplateIn, QcTemplateOut, QcTemplatePatch } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { toParamIn, useIdempotencyKey, validateParams } from "@/lib/quality";
import type { ParamDraft } from "@/lib/quality";
import { Button, FormDialog, Select, Switch, TextArea, TextField } from "@/ui";

interface QcTemplateFormDialogProps {
  /** Present = edit this template (code and kind are fixed once created). */
  template?: QcTemplateOut;
  onClose: () => void;
  onSaved: (template: QcTemplateOut) => void;
}

const KIND_OPTIONS: { value: QcKind; label: string }[] = [
  { value: "incoming", label: "Incoming (goods receipt)" },
  { value: "in-process", label: "In-process" },
  { value: "finished", label: "Finished goods" },
];

const PARAM_KIND_OPTIONS = [
  { value: "numeric", label: "Measured (number)" },
  { value: "pass_fail", label: "Pass / fail" },
];

let draftCounter = 0;
function newDraft(partial: Partial<ParamDraft> = {}): ParamDraft {
  draftCounter += 1;
  return {
    key: `draft-${draftCounter}`,
    name: "",
    kind: "numeric",
    unit: "",
    min: "",
    max: "",
    is_critical: false,
    ...partial,
  };
}

/**
 * Create or edit a QC template (`operations.quality.inspection.approve:ORG`). Bounds are typed as
 * text and sent as decimal strings; the server is the judge of what counts as acceptable, and an
 * edit replaces the whole parameter list while inspections already judged keep the bounds they
 * were judged against.
 */
export function QcTemplateFormDialog({ template, onClose, onSaved }: QcTemplateFormDialogProps) {
  const editing = template !== undefined;
  const [code, setCode] = useState(template?.code ?? "");
  const [name, setName] = useState(template?.name ?? "");
  const [kind, setKind] = useState<QcKind>(template?.kind ?? "incoming");
  const [description, setDescription] = useState(template?.description ?? "");
  const [isActive, setIsActive] = useState(template?.is_active ?? true);
  const [params, setParams] = useState<ParamDraft[]>(
    template
      ? template.params.map((p) =>
          newDraft({
            name: p.name,
            kind: p.kind,
            unit: p.unit,
            min: p.min ?? "",
            max: p.max ?? "",
            is_critical: p.is_critical,
          }),
        )
      : [newDraft()],
  );
  const [localError, setLocalError] = useState<string | undefined>(undefined);

  const paramsIn = params.map(toParamIn);
  const key = useIdempotencyKey(JSON.stringify({ code, name, kind, description, isActive, paramsIn }));

  const save = useMutation(
    (input: { create: QcTemplateIn } | { patch: QcTemplatePatch }) =>
      "create" in input
        ? createQcTemplate(input.create, key)
        : updateQcTemplate(template?.id ?? "", input.patch),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function patchParam(index: number, change: Partial<ParamDraft>) {
    setParams((rows) => rows.map((r, i) => (i === index ? { ...r, ...change } : r)));
  }

  function submit() {
    setLocalError(undefined);
    if (!editing && code.trim() === "") return setLocalError("Give the template a code, e.g. INC-FABRIC.");
    if (name.trim() === "") return setLocalError("Give the template a name.");
    const problem = validateParams(params);
    if (problem) return setLocalError(problem);
    if (editing) {
      void save.mutate({
        patch: { name: name.trim(), description, is_active: isActive, params: paramsIn },
      });
    } else {
      void save.mutate({
        create: {
          code: code.trim(),
          name: name.trim(),
          kind,
          description,
          is_active: isActive,
          params: paramsIn,
        },
      });
    }
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={editing ? `Edit ${template.code}` : "New QC template"}
      description="The checks an inspection is judged against. Mark a parameter critical when a failure must block release."
      submitLabel={editing ? "Save changes" : "Create template"}
      pendingLabel="Saving…"
      pending={save.pending}
      size="lg"
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          label="Code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          disabled={editing}
          required={!editing}
          maxLength={40}
          {...(save.fieldErrors.code !== undefined ? { error: save.fieldErrors.code } : {})}
        />
        <Select
          label="Kind"
          value={kind}
          onChange={(e) => setKind(e.target.value as QcKind)}
          options={KIND_OPTIONS}
          disabled={editing}
        />
      </div>
      <TextField
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        maxLength={160}
        {...(save.fieldErrors.name !== undefined ? { error: save.fieldErrors.name } : {})}
      />
      <TextArea
        label="Description"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        maxLength={500}
        optional
      />
      <Switch
        label="Active"
        checked={isActive}
        onChange={setIsActive}
        description={isActive ? "Offered when opening an inspection." : "Hidden from new inspections."}
      />

      <fieldset className="flex flex-col gap-3">
        <legend className="text-sm font-semibold text-ink">Parameters</legend>
        {params.length === 0 ? (
          <p className="text-sm text-muted">No parameters yet — add the first check below.</p>
        ) : null}
        {params.map((p, i) => (
          <div key={p.key} className="rounded-tile border border-line p-3 flex flex-col gap-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <TextField
                label={`Parameter ${i + 1} name`}
                value={p.name}
                onChange={(e) => patchParam(i, { name: e.target.value })}
                maxLength={80}
              />
              <Select
                label={`Parameter ${i + 1} type`}
                value={p.kind}
                onChange={(e) => patchParam(i, { kind: e.target.value as ParamDraft["kind"] })}
                options={PARAM_KIND_OPTIONS}
              />
              <TextField
                label={`Parameter ${i + 1} unit`}
                value={p.unit}
                onChange={(e) => patchParam(i, { unit: e.target.value })}
                maxLength={20}
                optional
              />
            </div>
            {p.kind === "numeric" ? (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <TextField
                  label={`Parameter ${i + 1} minimum`}
                  value={p.min}
                  onChange={(e) => patchParam(i, { min: e.target.value })}
                  inputMode="decimal"
                  placeholder="no lower limit"
                  optional
                />
                <TextField
                  label={`Parameter ${i + 1} maximum`}
                  value={p.max}
                  onChange={(e) => patchParam(i, { max: e.target.value })}
                  inputMode="decimal"
                  placeholder="no upper limit"
                  optional
                />
              </div>
            ) : null}
            <div className="flex items-center justify-between gap-3">
              <Switch
                label={`Parameter ${i + 1} is critical`}
                checked={p.is_critical}
                onChange={(v) => patchParam(i, { is_critical: v })}
                description={p.is_critical ? "A failure blocks release." : "A failure needs a note to release."}
              />
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setParams((rows) => rows.filter((_, j) => j !== i))}
              >
                Remove
              </Button>
            </div>
          </div>
        ))}
        <div>
          <Button type="button" variant="secondary" size="sm" onClick={() => setParams((r) => [...r, newDraft()])}>
            Add parameter
          </Button>
        </div>
      </fieldset>
      {localError ? (
        <p role="alert" className="text-sm text-danger">
          {localError}
        </p>
      ) : null}
    </FormDialog>
  );
}
