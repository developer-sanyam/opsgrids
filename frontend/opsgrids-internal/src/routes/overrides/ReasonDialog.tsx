import { useRef, useState } from "react";
import type { ReactNode } from "react";

import { useMutation } from "@opsgrids/client";
import { FormDialog, TextArea } from "@opsgrids/ui";

import { postOverride } from "@/api/client";
import type { OrganizationRow, OverrideResult } from "@/api/types";
import { referenceOf } from "@/lib/errors";
import { Code, DelegationNote } from "./parts";
import { failureText } from "./text";

type ReasonKind = "suspend" | "reactivate";

interface Copy {
  title: (name: string) => string;
  description: string;
  placeholder: string;
  submit: string;
  pending: string;
  tone: "primary" | "danger";
  note: ReactNode;
}

const COPY: Record<ReasonKind, Copy> = {
  suspend: {
    title: (name) => `Suspend ${name}`,
    description: "De-entitles the account on its next access-token refresh (≤ 15 min).",
    placeholder: "e.g. Non-payment after dunning window closed",
    submit: "Suspend account",
    pending: "Suspending…",
    tone: "danger",
    note: (
      <>
        POSTs <Code>override.suspend</Code> to <Code>/api/v1/admin/overrides</Code>, which delegates
        to subscription-service's <Code>/internal</Code> suspend. The activation is projected off,
        and the gateway then answers the customer with a 402.
      </>
    ),
  },
  reactivate: {
    title: (name) => `Reactivate ${name}`,
    description: "Restores entitlement; the customer regains access on the next token refresh.",
    placeholder: "e.g. Payment recovered — restoring access",
    submit: "Reactivate",
    pending: "Reactivating…",
    tone: "primary",
    note: (
      <>
        POSTs <Code>override.reactivate</Code> to <Code>/api/v1/admin/overrides</Code>;
        subscription-service moves the subscription back to <Code>active</Code> and re-projects the
        activation.
      </>
    ),
  },
};

interface ReasonDialogProps {
  kind: ReasonKind;
  org: OrganizationRow;
  onClose: () => void;
  onDone: (result: OverrideResult) => void;
}

/**
 * Suspend or reactivate one account: a reason, and nothing else. The reason is required and goes
 * into the audit row, so an empty one is refused here and again by the server; suspending is the
 * one override whose submit is itself the destructive act, so it wears the danger tone.
 *
 * Mounted only while open, so closing it resets it — never hoist the reason into the screen.
 */
export function ReasonDialog({ kind, org, onClose, onDone }: ReasonDialogProps) {
  const copy = COPY[kind];
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);
  const reasonRef = useRef<HTMLTextAreaElement>(null);

  const run = useMutation(
    (trimmed: string) => postOverride({ kind, organization_id: org.organization_id, reason: trimmed }),
    {
      onSuccess: (result) => {
        onClose();
        onDone(result);
      },
    },
  );

  function submit() {
    setReasonError(undefined);
    if (reason.trim().length === 0) {
      setReasonError("Give a reason — it goes into the audit log.");
      reasonRef.current?.focus();
      return;
    }
    void run.mutate(reason.trim());
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={copy.title(org.name)}
      description={copy.description}
      submitLabel={copy.submit}
      pendingLabel={copy.pending}
      submitTone={copy.tone}
      pending={run.pending}
      initialFocusRef={reasonRef}
      error={failureText(run.error, run.fieldErrors, ["reason"])}
      reference={referenceOf(run.error)}
    >
      <TextArea
        ref={reasonRef}
        label="Reason (required)"
        rows={3}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        placeholder={copy.placeholder}
        error={reasonError ?? run.fieldErrors.reason}
      />
      <DelegationNote>{copy.note}</DelegationNote>
    </FormDialog>
  );
}
