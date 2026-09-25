import { useRef, useState } from "react";

import { createIncident, listAssets } from "@/api/client";
import type { Incident, IncidentCategory, IncidentIn, Priority } from "@/api/types";
import { ApiError, problemMessage, useAuth, useCan, useMutation } from "@opsgrids/client";
import { Combobox, FormDialog, Select, TextArea, TextField, useFormDirty } from "@opsgrids/ui";
import type { ComboboxOption } from "@opsgrids/ui";

const PRIORITY_OPTIONS: { value: Priority; label: string }[] = [
  { value: "P1", label: "P1 · Critical" },
  { value: "P2", label: "P2 · High" },
  { value: "P3", label: "P3 · Moderate" },
  { value: "P4", label: "P4 · Low" },
];

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

interface RaiseIncidentDialogProps {
  onClose: () => void;
  onCreated: (incident: Incident) => void;
}

/** `POST /itsm/incidents`. Raises a new incident — `status` always starts `new` server-side. */
export function RaiseIncidentDialog({ onClose, onCreated }: RaiseIncidentDialogProps) {
  const { user } = useAuth();
  const [summary, setSummary] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<Priority>("P3");
  const [category, setCategory] = useState<IncidentCategory>("software");
  const [requester, setRequester] = useState(user?.full_name ?? "");
  const [assignee, setAssignee] = useState("");
  // Assigning is an organization-wide act: the service refuses an assignee from anyone who may
  // only work their own incidents, so the field is not offered to them at all.
  const canAssign = useCan("itsm.service.incident.write", "ORG");
  const [asset, setAsset] = useState<ComboboxOption | null>(null);

  const fields = { summary, description, priority, category, requester, assignee, assetId: asset?.id ?? "" };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [summaryError, setSummaryError] = useState<string | undefined>(undefined);
  const [requesterError, setRequesterError] = useState<string | undefined>(undefined);
  const summaryRef = useRef<HTMLInputElement>(null);

  const save = useMutation((input: IncidentIn) => createIncident(input), {
    onSuccess: (incident) => onCreated(incident),
  });

  function submit() {
    setSummaryError(undefined);
    setRequesterError(undefined);
    const trimmedSummary = summary.trim();
    if (trimmedSummary.length < 1) {
      setSummaryError("Describe what's wrong.");
      summaryRef.current?.focus();
      return;
    }
    if (requester.trim().length < 1) {
      setRequesterError("Who is this for?");
      return;
    }
    void save.mutate({
      summary: trimmedSummary,
      description: description.trim(),
      priority,
      category,
      requester: requester.trim(),
      assignee: canAssign ? assignee.trim() : "",
      affected_asset_tag: asset?.id ?? null,
    });
  }

  // The 404 for an unknown asset tag carries no `field` on the wire — `NotFoundProblem` is a
  // reference lookup failure, not a shape validation the platform's `errors[]` convention covers
  // — so a caller who had picked an asset gets it read as that field's error rather than a
  // top-of-dialog banner it did not ask about.
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
      title="Raise an incident"
      submitLabel="Raise incident"
      pendingLabel="Raising…"
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
      <div className="grid gap-4 sm:grid-cols-2">
        <Select
          label="Priority"
          value={priority}
          onChange={(e) => setPriority(e.target.value as Priority)}
          options={PRIORITY_OPTIONS}
          error={save.fieldErrors.priority}
        />
        <Select
          label="Category"
          value={category}
          onChange={(e) => setCategory(e.target.value as IncidentCategory)}
          options={CATEGORY_OPTIONS}
          error={save.fieldErrors.category}
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Requester"
          value={requester}
          onChange={(e) => setRequester(e.target.value)}
          maxLength={120}
          helper="Who this is raised for."
          required
          error={requesterError ?? save.fieldErrors.requester}
        />
        {canAssign ? (
          <TextField
            label="Assignee"
            value={assignee}
            onChange={(e) => setAssignee(e.target.value)}
            optional
            maxLength={120}
            helper="No agent directory yet — a free-text name."
            error={save.fieldErrors.assignee}
          />
        ) : null}
      </div>
      <Combobox
        label="Affected asset"
        value={asset}
        onChange={setAsset}
        loadOptions={loadAssetOptions}
        placeholder="Search the CMDB by tag or name…"
        helper="Optional. Leave blank if nothing specific is affected."
        error={assetError}
        copy={{
          idle: "Start typing an asset tag or name.",
          noMatches: (q) => `No asset matches “${q}”.`,
        }}
      />
    </FormDialog>
  );
}
