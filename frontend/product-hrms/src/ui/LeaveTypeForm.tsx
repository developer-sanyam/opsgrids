import { useRef, useState } from "react";

import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Switch, TextField } from "@opsgrids/ui";

import { createLeaveType, updateLeaveType } from "@/api/leave";
import type { LeaveTypeIn, LeaveTypeOut, LeaveTypePatch } from "@/api/types";

interface LeaveTypeFormProps {
  leaveType: LeaveTypeOut | null;
  onClose: () => void;
  onSaved: (leaveType: LeaveTypeOut) => void;
}

/** Blank means "no cap" — the same reading `services/leave/app/schemas.py`'s
 *  `annual_quota_half_days` etc. give a `null` value. */
function parseOptionalCount(value: string): number | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : Number(trimmed);
}

export function LeaveTypeForm({ leaveType, onClose, onSaved }: LeaveTypeFormProps) {
  const editing = leaveType !== null;
  const [code, setCode] = useState(leaveType?.code ?? "");
  const [name, setName] = useState(leaveType?.name ?? "");
  const [isPaid, setIsPaid] = useState(leaveType?.is_paid ?? true);
  const [annualQuota, setAnnualQuota] = useState(
    leaveType?.annual_quota_half_days != null ? String(leaveType.annual_quota_half_days) : "",
  );
  const [monthlyAccrual, setMonthlyAccrual] = useState(
    leaveType?.monthly_accrual_half_days != null ? String(leaveType.monthly_accrual_half_days) : "",
  );
  const [carryForwardCap, setCarryForwardCap] = useState(
    leaveType?.carry_forward_cap_half_days != null
      ? String(leaveType.carry_forward_cap_half_days)
      : "",
  );

  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const codeRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  // Compared against the same seed the fields above initialised from — this form unmounts on
  // close (FormDialog's resetting-is-structural note), so `leaveType` never changes under it.
  const dirty =
    code !== (leaveType?.code ?? "") ||
    name !== (leaveType?.name ?? "") ||
    isPaid !== (leaveType?.is_paid ?? true) ||
    annualQuota !== (leaveType?.annual_quota_half_days != null ? String(leaveType.annual_quota_half_days) : "") ||
    monthlyAccrual !== (leaveType?.monthly_accrual_half_days != null ? String(leaveType.monthly_accrual_half_days) : "") ||
    carryForwardCap !== (leaveType?.carry_forward_cap_half_days != null ? String(leaveType.carry_forward_cap_half_days) : "");

  const save = useMutation(
    (input: LeaveTypeIn | LeaveTypePatch) =>
      leaveType
        ? updateLeaveType(leaveType.id, input as LeaveTypePatch)
        : createLeaveType(input as LeaveTypeIn),
    { onSuccess: (saved) => { onClose(); onSaved(saved); } },
  );

  function submit() {
    setCodeError(undefined);
    setNameError(undefined);
    if (!editing && !/^[A-Za-z0-9_-]{1,40}$/.test(code.trim())) {
      setCodeError("1–40 characters: letters, numbers, hyphens and underscores only.");
      codeRef.current?.focus();
      return;
    }
    if (name.trim().length < 1) {
      setNameError("Name is required.");
      nameRef.current?.focus();
      return;
    }
    const shared = {
      name: name.trim(),
      is_paid: isPaid,
      annual_quota_half_days: parseOptionalCount(annualQuota),
      monthly_accrual_half_days: parseOptionalCount(monthlyAccrual),
      carry_forward_cap_half_days: parseOptionalCount(carryForwardCap),
    };
    if (!editing) {
      void save.mutate({ code: code.trim(), ...shared } satisfies LeaveTypeIn);
      return;
    }
    void save.mutate(shared satisfies LeaveTypePatch);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={editing ? `Edit ${leaveType.name}` : "New leave type"}
      submitLabel={editing ? "Save changes" : "Create leave type"}
      pendingLabel={editing ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={editing ? nameRef : codeRef}
      dirty={dirty}
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? {
            error: problemMessage(save.error),
            ...(save.error instanceof ApiError && save.error.problem?.request_id
              ? { reference: save.error.problem.request_id }
              : {}),
          }
        : {})}
    >
      <TextField
        ref={codeRef}
        label="Code"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        disabled={editing}
        required
        helper={editing ? "Immutable — a rename is a new type." : "Letters, numbers, hyphens and underscores."}
        error={codeError ?? save.fieldErrors.code}
      />
      <TextField
        ref={nameRef}
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        error={nameError ?? save.fieldErrors.name}
      />
      <Switch
        label="Paid leave"
        description="Unpaid types (like loss-of-pay) never check the employee's balance."
        checked={isPaid}
        onChange={setIsPaid}
      />
      <TextField
        label="Annual quota (half-days)"
        type="number"
        min={0}
        value={annualQuota}
        onChange={(e) => setAnnualQuota(e.target.value)}
        optional
        helper="Leave blank for no fixed quota."
        error={save.fieldErrors.annual_quota_half_days}
      />
      <TextField
        label="Monthly accrual (half-days)"
        type="number"
        min={0}
        value={monthlyAccrual}
        onChange={(e) => setMonthlyAccrual(e.target.value)}
        optional
        helper="Leave blank for no automatic monthly accrual."
        error={save.fieldErrors.monthly_accrual_half_days}
      />
      <TextField
        label="Carry-forward cap (half-days)"
        type="number"
        min={0}
        value={carryForwardCap}
        onChange={(e) => setCarryForwardCap(e.target.value)}
        optional
        helper="Leave blank for no cap."
        error={save.fieldErrors.carry_forward_cap_half_days}
      />
    </FormDialog>
  );
}
