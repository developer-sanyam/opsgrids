import { useRef, useState } from "react";

import { createRequest } from "@/api/client";
import type { CatalogItem, ServiceRequest, ServiceRequestIn } from "@/api/types";
import { ApiError, problemMessage, useAuth, useMutation } from "@opsgrids/client";
import { FormDialog, Select, TextField, useFormDirty } from "@opsgrids/ui";

interface RaiseRequestDialogProps {
  catalog: CatalogItem[];
  /** Preselected when opened from a catalog card. */
  preselected: CatalogItem | null;
  onClose: () => void;
  onCreated: (request: ServiceRequest) => void;
}

/** `POST /itsm/service-requests`. `status` always starts `approval` server-side. */
export function RaiseRequestDialog({
  catalog,
  preselected,
  onClose,
  onCreated,
}: RaiseRequestDialogProps) {
  const { user } = useAuth();
  const [catalogItemId, setCatalogItemId] = useState(preselected?.id ?? catalog[0]?.id ?? "");
  const [subject, setSubject] = useState("");
  const [requester, setRequester] = useState(user?.full_name ?? "");

  const fields = { catalogItemId, subject, requester };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [subjectError, setSubjectError] = useState<string | undefined>(undefined);
  const [requesterError, setRequesterError] = useState<string | undefined>(undefined);
  const subjectRef = useRef<HTMLInputElement>(null);

  const save = useMutation((input: ServiceRequestIn) => createRequest(input), {
    onSuccess: (request) => onCreated(request),
  });

  function submit() {
    setSubjectError(undefined);
    setRequesterError(undefined);
    const trimmedSubject = subject.trim();
    if (trimmedSubject.length < 1) {
      setSubjectError("Say who or what this is for.");
      subjectRef.current?.focus();
      return;
    }
    if (requester.trim().length < 1) {
      setRequesterError("Who is raising this?");
      return;
    }
    void save.mutate({
      catalog_item_id: catalogItemId,
      subject: trimmedSubject,
      requester: requester.trim(),
    });
  }

  const hasTopLevelError = save.error && Object.keys(save.fieldErrors).length === 0;
  const selected = catalog.find((c) => c.id === catalogItemId);

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Raise a request"
      submitLabel="Raise request"
      pendingLabel="Raising…"
      pending={save.pending}
      initialFocusRef={subjectRef}
      description={selected ? `SLA target: ${selected.sla_days} working day(s).` : undefined}
      dirty={dirty}
      error={hasTopLevelError ? problemMessage(save.error) : undefined}
      reference={hasTopLevelError ? (save.error as ApiError).problem?.request_id : undefined}
    >
      <Select
        label="Request type"
        value={catalogItemId}
        onChange={(e) => setCatalogItemId(e.target.value)}
        options={catalog.map((c) => ({ value: c.id, label: c.name }))}
        required
        error={save.fieldErrors.catalog_item_id}
      />
      <TextField
        ref={subjectRef}
        label="Who or what this is for"
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        maxLength={200}
        placeholder="e.g. the new hire's name and start date"
        required
        error={subjectError ?? save.fieldErrors.subject}
      />
      <TextField
        label="Requester"
        value={requester}
        onChange={(e) => setRequester(e.target.value)}
        maxLength={120}
        required
        error={requesterError ?? save.fieldErrors.requester}
      />
    </FormDialog>
  );
}
