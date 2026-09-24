/**
 * `POST /leave/requests` — self-service, always your own leave. Calendar dates only: the two
 * fields are plain `<input type="date">`, never a zoned instant — the employee names the day
 * they mean directly, the same "never derived from a UTC clock" rule
 * `services/leave/app/models.py`'s module docstring states for the server side of this same
 * write (the timezone defect this rebuild does not repeat).
 */
import { useEffect, useState } from "react";

import { problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Select, Switch, TextArea, TextField } from "@opsgrids/ui";

import { applyForLeave, listLeaveTypes } from "@/api/leave";
import type { LeaveRequestOut, LeaveTypeOut } from "@/api/types";

interface ApplyLeaveDialogProps {
  onClose: () => void;
  onSaved: (request: LeaveRequestOut) => void;
}

export function ApplyLeaveDialog({ onClose, onSaved }: ApplyLeaveDialogProps) {
  const [leaveTypes, setLeaveTypes] = useState<LeaveTypeOut[]>([]);
  const [leaveTypeId, setLeaveTypeId] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [halfDayStart, setHalfDayStart] = useState(false);
  const [halfDayEnd, setHalfDayEnd] = useState(false);
  const [reason, setReason] = useState("");

  useEffect(() => {
    let active = true;
    listLeaveTypes({ size: 100 }).then((page) => {
      if (!active) return;
      setLeaveTypes(page.items);
      if (page.items[0]) setLeaveTypeId((current) => current || page.items[0]!.id);
    });
    return () => {
      active = false;
    };
  }, []);

  const singleDay = startDate !== "" && startDate === endDate;

  const save = useMutation(
    () =>
      applyForLeave({
        leave_type_id: leaveTypeId,
        start_date: startDate,
        end_date: endDate,
        half_day_start: halfDayStart,
        // A single-day request is one flag describing one day — keep both flags in step so the
        // server's own `single_day_half_flags_agree` rule is never the surprise that refuses it.
        half_day_end: singleDay ? halfDayStart : halfDayEnd,
        reason: reason.trim(),
      }),
    { onSuccess: (saved) => { onClose(); onSaved(saved); } },
  );

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={() => void save.mutate(undefined)}
      title="Apply for leave"
      submitLabel="Submit request"
      pendingLabel="Submitting…"
      pending={save.pending}
      {...(save.error ? { error: problemMessage(save.error) } : {})}
    >
      <Select
        label="Leave type"
        value={leaveTypeId}
        onChange={(e) => setLeaveTypeId(e.target.value)}
        options={leaveTypes.map((t) => ({ value: t.id, label: `${t.name} (${t.code})` }))}
        error={save.fieldErrors.leave_type_id}
      />
      <TextField
        label="Start date"
        type="date"
        value={startDate}
        onChange={(e) => setStartDate(e.target.value)}
        error={save.fieldErrors.start_date}
      />
      <TextField
        label="End date"
        type="date"
        value={endDate}
        min={startDate || undefined}
        onChange={(e) => setEndDate(e.target.value)}
        error={save.fieldErrors.end_date}
      />
      <Switch
        label={singleDay ? "Half day" : "Half day at the start"}
        checked={halfDayStart}
        onChange={setHalfDayStart}
      />
      {!singleDay ? (
        <Switch label="Half day at the end" checked={halfDayEnd} onChange={setHalfDayEnd} />
      ) : null}
      <TextArea
        label="Reason"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        maxLength={500}
        counterFrom={400}
        error={save.fieldErrors.reason}
      />
    </FormDialog>
  );
}
