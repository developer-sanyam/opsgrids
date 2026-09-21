/**
 * `POST /attendance/{id}/regularize` — the HR direct correction (gap 6), floored at ORG. Distinct
 * from `RequestCorrectionDialog`: that one is self-service and routes through approval; this one
 * is an immediate, audited override with no workflow, offered only to a holder of
 * `hrms.time.attendance.write` at ORG.
 */
import { useState } from "react";

import { problemMessage, useMutation } from "@opsgrids/client";
import { Button, Dialog, InlineAlert, RadioGroup, TextArea } from "@opsgrids/ui";

import { regularizeAttendance } from "@/api/client";
import type { AttendanceRecord, AttendanceStatus } from "@/api/types";
import { attendanceStatusLabel } from "@/lib/format";

interface CorrectAttendanceDialogProps {
  record: AttendanceRecord;
  onClose: () => void;
  onSaved: () => void;
}

export function CorrectAttendanceDialog({ record, onClose, onSaved }: CorrectAttendanceDialogProps) {
  const [to, setTo] = useState<AttendanceStatus | null>(null);
  const [reason, setReason] = useState("");
  const [choiceError, setChoiceError] = useState<string | undefined>(undefined);
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);

  const save = useMutation(
    (input: { to: AttendanceStatus; reason: string }) => regularizeAttendance(record.id, input),
    { onSuccess: () => { onClose(); onSaved(); } },
  );

  function submit() {
    setChoiceError(undefined);
    setReasonError(undefined);
    if (!to) {
      setChoiceError("Choose the correct status.");
      return;
    }
    if (!reason.trim()) {
      setReasonError("Reason cannot be blank.");
      return;
    }
    if (reason.length > 500) {
      setReasonError("Reason must be at most 500 characters.");
      return;
    }
    void save.mutate({ to, reason });
  }

  const options = record.allowed_transitions.map((status) => ({
    value: status,
    label: attendanceStatusLabel(status),
  }));

  return (
    <Dialog
      open
      onClose={() => {
        if (!save.pending) onClose();
      }}
      title={`Correct ${record.full_name}'s status`}
      description={`${record.work_date} · currently ${attendanceStatusLabel(record.status)}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={save.pending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} loading={save.pending}>
            {save.pending ? "Saving…" : "Save correction"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <RadioGroup
          legend="Correct status to"
          name="hr-correction"
          value={to}
          onChange={(v) => setTo(v as AttendanceStatus)}
          options={options}
          {...(choiceError !== undefined ? { error: choiceError } : {})}
        />
        <TextArea
          label="Reason"
          helper="Recorded on the audit trail."
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          maxLength={500}
          counterFrom={400}
          {...(reasonError !== undefined ? { error: reasonError } : {})}
        />
        {save.error ? <InlineAlert tone="danger" title={problemMessage(save.error)} /> : null}
      </div>
    </Dialog>
  );
}
