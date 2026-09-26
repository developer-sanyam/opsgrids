/** `POST /leave/balances/grant` — `hrms.time.leave_balance.write` at ORG. HR posts a credit
 *  (grant, accrual or adjustment) to one employee's ledger for one leave type. */
import { useState } from "react";

import { problemMessage, useMutation } from "@opsgrids/client";
import type { ComboboxOption } from "@opsgrids/ui";
import { FormDialog, Select, TextArea, TextField } from "@opsgrids/ui";

import { grantLeaveBalance } from "@/api/leave";
import type { GrantableLeaveEntryType, LeaveLedgerEntryOut, LeaveTypeOut } from "@/api/types";
import { EmployeePicker } from "@/ui/EmployeePicker";

const ENTRY_TYPE_OPTIONS: { value: GrantableLeaveEntryType; label: string }[] = [
  { value: "grant", label: "Grant" },
  { value: "accrual", label: "Accrual" },
  { value: "adjustment", label: "Adjustment" },
];

interface GrantLeaveBalanceDialogProps {
  leaveTypes: LeaveTypeOut[];
  onClose: () => void;
  onSaved: (entry: LeaveLedgerEntryOut) => void;
}

export function GrantLeaveBalanceDialog({
  leaveTypes,
  onClose,
  onSaved,
}: GrantLeaveBalanceDialogProps) {
  const [employee, setEmployee] = useState<ComboboxOption | null>(null);
  const [leaveTypeId, setLeaveTypeId] = useState(leaveTypes[0]?.id ?? "");
  const [entryType, setEntryType] = useState<GrantableLeaveEntryType>("grant");
  const [halfDays, setHalfDays] = useState("");
  const [note, setNote] = useState("");
  // Generated once, when the dialog opens — not per submit attempt — so a retried request (a
  // client timeout, a double-click on "Grant") reaches the server with the *same* key and is
  // recognised as the same grant rather than posting a second credit. A key minted fresh on
  // every submit would defeat the server's own idempotency check entirely.
  const [idempotencyKey] = useState(() => crypto.randomUUID());

  // Create-only dialog: dirty against the blank state it opened on.
  const dirty =
    employee !== null ||
    leaveTypeId !== (leaveTypes[0]?.id ?? "") ||
    entryType !== "grant" ||
    halfDays !== "" ||
    note !== "";

  const save = useMutation(
    () =>
      grantLeaveBalance({
        employee_id: employee?.id ?? "",
        leave_type_id: leaveTypeId,
        entry_type: entryType,
        half_days: Number(halfDays),
        note: note.trim() || null,
        idempotency_key: idempotencyKey,
      }),
    { onSuccess: (saved) => { onClose(); onSaved(saved); } },
  );

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={() => void save.mutate(undefined)}
      title="Grant a leave balance"
      submitLabel="Grant"
      pendingLabel="Granting…"
      pending={save.pending}
      dirty={dirty}
      {...(save.error ? { error: problemMessage(save.error) } : {})}
    >
      <EmployeePicker
        label="Employee"
        value={employee}
        onChange={setEmployee}
        error={save.fieldErrors.employee_id}
      />
      <Select
        label="Leave type"
        value={leaveTypeId}
        onChange={(e) => setLeaveTypeId(e.target.value)}
        options={leaveTypes.map((t) => ({ value: t.id, label: `${t.name} (${t.code})` }))}
        required
        error={save.fieldErrors.leave_type_id}
      />
      <Select
        label="Entry type"
        value={entryType}
        onChange={(e) => setEntryType(e.target.value as GrantableLeaveEntryType)}
        options={ENTRY_TYPE_OPTIONS}
        error={save.fieldErrors.entry_type}
      />
      <TextField
        label="Half-days"
        type="number"
        min={1}
        value={halfDays}
        onChange={(e) => setHalfDays(e.target.value)}
        helper="Always a credit — this endpoint only adds to the balance."
        required
        error={save.fieldErrors.half_days}
      />
      <TextArea
        label="Note"
        optional
        value={note}
        onChange={(e) => setNote(e.target.value)}
        maxLength={500}
        error={save.fieldErrors.note}
      />
    </FormDialog>
  );
}
