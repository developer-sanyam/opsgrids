import { useRef, useState } from "react";

import { listAssets, updateIncident } from "@/api/client";
import type { Incident, IncidentCategory, IncidentUpdate } from "@/api/types";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { Combobox, FormDialog, Select, TextArea, TextField, useFormDirty } from "@opsgrids/ui";
import type { ComboboxOption } from "@opsgrids/ui";

const CATEGORY_OPTIONS: { value: IncidentCategory; label: string }[] = [
  { value: "network", label: "Network" },
  { value: "hardware", label: "Hardware" },
  { value: "software", label: "Software" },
  { value: "access", label: "Access" },
  { value: "email", label: "Email" },
  { value: "security", label: "Security" },
];

async function loadAssetOptions(query: string, signal: AbortSignal): Promise<ComboboxOption[]> {
  const page = await listAssets({ search: query, size: 20 });
  if (signal.aborted) return [];
  return page.items.map((a) => ({ id: a.tag, label: a.name, sublabel: a.tag }));
}

interface EditIncidentDialogProps {
  incident: Incident;
  onClose: () => void;
  onSaved: (incident: Incident) => void;
}

/** `PATCH /itsm/incidents/{number}` — summary, description, category and affected asset. Priority
 *  and assignee live on their own ORG-gated controls in Properties, each its own PATCH. */
export function EditIncidentDialog({ incident, onClose, onSaved }: EditIncidentDialogProps) {
  const [summary, setSummary] = useState(incident.summary);
  const [description, setDescription] = useState(incident.description);
  const [category, setCategory] = useState<IncidentCategory>(incident.category);
  const [asset, setAsset] = useState<ComboboxOption | null>(
    incident.affected_asset_tag
      ? { id: incident.affected_asset_tag, label: incident.affected_asset_tag }
      : null,
  );

  const fields = { summary, description, category, assetId: asset?.id ?? "" };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [summaryError, setSummaryError] = useState<string | undefined>(undefined);
  const summaryRef = useRef<HTMLInputElement>(null);

  const save = useMutation((input: IncidentUpdate) => updateIncident(incident.id, input), {
    onSuccess: (updated) => onSaved(updated),
  });

  function submit() {
    setSummaryError(undefined);
    const trimmedSummary = summary.trim();
    if (trimmedSummary.length < 1) {
      setSummaryError("Describe what's wrong.");
      summaryRef.current?.focus();
      return;
    }
    void save.mutate({
      summary: trimmedSummary,
      description: description.trim(),
      category,
      affected_asset_tag: asset?.id ?? null,
    });
  }

  // The 404 for an unknown asset tag carries no `field` on the wire, so it is read here rather
  // than surfaced as a top-of-dialog banner about a field the caller did not ask about.
  const isMissingAssetError =
    save.error instanceof ApiError && save.error.status === 404 && asset !== null;
  const assetError = save.fieldErrors.affected_asset_tag ?? (isMissingAssetError ? problemMessage(save.error) : undefined);
  const hasTopLevelError =
    save.error && Object.keys(save.fieldErrors).length === 0 && !isMissingAssetError;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Edit ${incident.id}`}
      submitLabel="Save changes"
      pendingLabel="Saving…"
      pending={save.pending}
      initialFocusRef={summaryRef}
      size="lg"
      dirty={dirty}
      error={hasTopLevelError ? problemMessage(save.error) : undefined}
      reference={hasTopLevelError ? (save.error as ApiError).problem?.request_id : undefined}
    >
      <TextField
        ref={summaryRef}
        label="Summary"
        value={summary}
        onChange={(e) => setSummary(e.target.value)}
        maxLength={300}
        required
        error={summaryError ?? save.fieldErrors.summary}
      />
      <TextArea
        label="Description"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        optional
        maxLength={8000}
        error={save.fieldErrors.description}
      />
      <Select
        label="Category"
        value={category}
        onChange={(e) => setCategory(e.target.value as IncidentCategory)}
        options={CATEGORY_OPTIONS}
        error={save.fieldErrors.category}
      />
      <Combobox
        label="Affected asset"
        value={asset}
        onChange={setAsset}
        loadOptions={loadAssetOptions}
        placeholder="Search the CMDB by tag or name…"
        helper="Optional. Clear it if nothing specific is affected."
        error={assetError}
      />
    </FormDialog>
  );
}
