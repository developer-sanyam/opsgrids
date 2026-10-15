import { useState } from "react";

import { convertLead } from "@/api/client";
import type { Lead, LeadConvertResult } from "@/api/types";
import { STAGES, isOpen } from "@/data/stages";
import { maybe } from "@/lib/optional";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, InlineAlert, parseMinor, Select, TextField, useFormDirty } from "@opsgrids/ui";

interface ConvertLeadDialogProps {
  lead: Lead;
  onClose: () => void;
  onConverted: (result: LeadConvertResult) => void;
}

/** The stages a deal can open in. A conversion never opens a deal already won or lost. */
const OPEN_STAGES = STAGES.filter((s) => isOpen(s.stage)).map((s) => ({ value: s.stage, label: s.label }));

/**
 * Convert a qualified lead -- `POST /leads/{id}:convert`.
 *
 * The conversion makes a company, a contact when the lead has a way to reach them (an email, a
 * phone, or both; the contact carries the lead's number sealed, never in the clear) and a deal.
 * The rep may say what the deal is worth and where it starts; both are optional, and leaving them
 * out opens a zero-value deal in the pipeline's first stage, as before. A name for the deal is not
 * asked for: it is "<Company> -- <Lead name>", numbered by the service if that name is taken.
 */
export function ConvertLeadDialog({ lead, onClose, onConverted }: ConvertLeadDialogProps) {
  const [value, setValue] = useState("");
  const [stage, setStage] = useState("");
  const [valueError, setValueError] = useState<string | undefined>(undefined);

  const fields = { value, stage };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const hasContact = lead.email !== null || lead.phone !== null;

  const convert = useMutation(
    (input: { value_minor: number }) =>
      convertLead(lead.id, {
        ...(input.value_minor > 0 ? { deal_value_minor: input.value_minor } : {}),
        ...(stage !== "" ? { deal_stage: stage } : {}),
      }),
    { onSuccess: (result) => onConverted(result) },
  );

  function submit() {
    setValueError(undefined);
    let valueMinor = 0;
    if (value.trim() !== "") {
      const parsed = parseMinor(value, "INR");
      if (!parsed.ok) {
        setValueError(parsed.message);
        return;
      }
      valueMinor = parsed.minor;
    }
    void convert.mutate({ value_minor: valueMinor });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Convert this lead?"
      submitLabel="Convert"
      pendingLabel="Converting…"
      pending={convert.pending}
      dirty={dirty}
      {...(convert.error && Object.keys(convert.fieldErrors).length === 0
        ? {
            error: problemMessage(convert.error),
            ...maybe("reference", (convert.error as ApiError).problem?.request_id),
          }
        : {})}
    >
      <InlineAlert tone="info" live="off" title="What this creates">
        {lead.full_name} becomes a company{hasContact ? ", a contact" : ""} and a deal. This can't be
        undone.
      </InlineAlert>
      <TextField
        label="Deal value (₹)"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        inputMode="decimal"
        optional
        placeholder="0"
        helper="What you expect this deal to be worth. You can change it later."
        {...maybe("error", valueError ?? convert.fieldErrors.deal_value_minor)}
      />
      <Select
        label="Deal stage"
        value={stage}
        onChange={(e) => setStage(e.target.value)}
        placeholder="Start at the first stage"
        options={OPEN_STAGES}
        {...maybe("error", convert.fieldErrors.deal_stage)}
      />
    </FormDialog>
  );
}
