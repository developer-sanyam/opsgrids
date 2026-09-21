/**
 * `POST /employees/{id}/transition` — the only way an employee's lifecycle status moves. The
 * target is limited client-side to what `domain.py`'s state machine allows from the record's
 * *current* status (probation→active|exited; active→on_notice|exited; on_notice→active|exited;
 * exited is terminal) — the server enforces the same machine regardless, so this is a courtesy,
 * not the check.
 */
import { useRef, useState } from "react";

import { problemMessage, useMutation } from "@opsgrids/client";
import { ConfirmDialog, RadioGroup, TextArea } from "@opsgrids/ui";

import { transitionEmployee } from "@/api/client";
import type { EmployeeOut, EmployeeTransitionTarget } from "@/api/types";
import { employeeStatusLabel } from "@/lib/format";

const TARGETS: Record<EmployeeOut["status"], EmployeeTransitionTarget[]> = {
  probation: ["active", "exited"],
  active: ["on_notice", "exited"],
  on_notice: ["active", "exited"],
  exited: [],
};

interface TransitionDialogProps {
  employee: EmployeeOut;
  onClose: () => void;
  onSaved: (employee: EmployeeOut) => void;
}

export function TransitionDialog({ employee, onClose, onSaved }: TransitionDialogProps) {
  const [to, setTo] = useState<EmployeeTransitionTarget | null>(null);
  const [reason, setReason] = useState("");
  const [choiceError, setChoiceError] = useState<string | undefined>(undefined);
  const reasonRef = useRef<HTMLTextAreaElement>(null);

  const save = useMutation(
    (input: { to: EmployeeTransitionTarget; reason: string }) =>
      transitionEmployee(employee.id, { to: input.to, reason: input.reason || null }),
    { onSuccess: (saved) => { onClose(); onSaved(saved); } },
  );

  const options = TARGETS[employee.status].map((status) => ({
    value: status,
    label: employeeStatusLabel(status),
  }));

  function submit() {
    setChoiceError(undefined);
    if (!to) {
      setChoiceError("Choose the new status.");
      return;
    }
    void save.mutate({ to, reason });
  }

  const confirmLabel = to === "exited" ? "Mark as exited" : "Change status";

  return (
    <ConfirmDialog
      open
      onClose={onClose}
      onConfirm={submit}
      title={`Change ${employee.full_name}'s status`}
      body={
        to === "exited"
          ? "This is irreversible — exited is a terminal status with no way back through this screen. Re-hiring is a new record, not a return from this one."
          : "The new status takes effect immediately."
      }
      confirmLabel={confirmLabel}
      pendingLabel="Saving…"
      tone={to === "exited" ? "danger" : "primary"}
      pending={save.pending}
      {...(save.error ? { error: problemMessage(save.error) } : {})}
    >
      <div className="flex flex-col gap-4">
        <RadioGroup
          legend="Change to"
          name="employee-transition"
          value={to}
          onChange={(v) => setTo(v as EmployeeTransitionTarget)}
          options={options}
          {...(choiceError !== undefined ? { error: choiceError } : {})}
        />
        <TextArea
          ref={reasonRef}
          label="Reason"
          helper="Kept with the employee's record."
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          maxLength={500}
          counterFrom={400}
          optional
          error={save.fieldErrors.reason}
        />
      </div>
    </ConfirmDialog>
  );
}
