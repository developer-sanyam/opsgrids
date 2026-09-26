import { useRef, useState } from "react";

import { createAudience, updateAudience } from "@/api/client";
import type { Audience, AudienceIn, AudiencePatch } from "@/api/types";
import { today } from "@/lib/now";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, TextArea, TextField } from "@/ui";

interface AudienceFormDialogProps {
  /** `null` creates. */
  audience: Audience | null;
  onClose: () => void;
  onSaved: (audience: Audience) => void;
}

/**
 * Create or edit a saved audience segment — `POST /marketing/audiences` /
 * `PATCH /marketing/audiences/{id}`. One component for both; mounted only while its dialog is
 * open, so closing it is the reset.
 *
 * `created_at` is the segment's *definition* date, not a timestamp — it defaults to today and is
 * always required, so there is no way to submit without one.
 */
export function AudienceFormDialog({ audience, onClose, onSaved }: AudienceFormDialogProps) {
  const [name, setName] = useState(audience?.name ?? "");
  const [size, setSize] = useState(String(audience?.size ?? ""));
  const [criteria, setCriteria] = useState(audience?.criteria ?? "");
  const [definedOn, setDefinedOn] = useState(audience?.created_at ?? today());

  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [sizeError, setSizeError] = useState<string | undefined>(undefined);
  const [definedOnError, setDefinedOnError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  // Compared against the same seed the fields above initialised from — this form unmounts on
  // close (FormDialog's resetting-is-structural note), so `audience` never changes under it.
  const dirty =
    name !== (audience?.name ?? "") ||
    size !== String(audience?.size ?? "") ||
    criteria !== (audience?.criteria ?? "") ||
    definedOn !== (audience?.created_at ?? today());

  const save = useMutation(
    (input: AudienceIn | AudiencePatch) =>
      audience
        ? updateAudience(audience.id, input as AudiencePatch)
        : createAudience(input as AudienceIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setNameError(undefined);
    setSizeError(undefined);
    setDefinedOnError(undefined);

    const trimmedName = name.trim();
    if (trimmedName.length < 1) {
      setNameError("Give the segment a name.");
      nameRef.current?.focus();
      return;
    }
    if (trimmedName.length > 160) {
      setNameError("Keep the name to 160 characters or fewer.");
      nameRef.current?.focus();
      return;
    }
    const sizeValue = Number(size);
    if (size.trim() === "" || !Number.isFinite(sizeValue) || !Number.isInteger(sizeValue) || sizeValue < 0) {
      setSizeError("Enter the number of reachable people, 0 or more.");
      return;
    }
    if (definedOn.trim() === "") {
      setDefinedOnError("Choose the date this segment was defined.");
      return;
    }

    if (audience) {
      const patch: AudiencePatch = {
        name: trimmedName,
        size: sizeValue,
        criteria: criteria.trim(),
        created_at: definedOn,
      };
      void save.mutate(patch);
      return;
    }

    const input: AudienceIn = {
      name: trimmedName,
      size: sizeValue,
      criteria: criteria.trim(),
      created_at: definedOn,
    };
    void save.mutate(input);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={audience ? `Edit ${audience.name}` : "New audience"}
      submitLabel={audience ? "Save changes" : "Create audience"}
      pendingLabel={audience ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={nameRef}
      dirty={dirty}
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? {
            error: problemMessage(save.error),
            ...((save.error as ApiError).problem?.request_id !== undefined
              ? { reference: (save.error as ApiError).problem?.request_id }
              : {}),
          }
        : {})}
    >
      <TextField
        ref={nameRef}
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        error={nameError ?? save.fieldErrors.name}
      />
      <TextField
        label="Reachable people"
        inputMode="numeric"
        value={size}
        onChange={(e) => setSize(e.target.value)}
        helper="How many people in this segment can be reached."
        required
        error={sizeError ?? save.fieldErrors.size}
      />
      <TextField
        label="Defined on"
        type="date"
        value={definedOn}
        onChange={(e) => setDefinedOn(e.target.value)}
        helper="The date this segment's definition was set — not today's date, unless that's when it happened."
        required
        error={definedOnError ?? save.fieldErrors.created_at}
      />
      <TextArea
        label="Criteria"
        value={criteria}
        onChange={(e) => setCriteria(e.target.value)}
        optional
        rows={3}
        maxLength={400}
        counterFrom={320}
        helper="A one-line human summary — firmographics, behaviour, lifecycle stage."
        error={save.fieldErrors.criteria}
      />
    </FormDialog>
  );
}
