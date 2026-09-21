import { useRef, useState } from "react";

import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Switch, TextField } from "@opsgrids/ui";

import { createShift, updateShift } from "@/api/client";
import type { ShiftIn, ShiftOut, ShiftPatch } from "@/api/types";

interface ShiftFormProps {
  shift: ShiftOut | null;
  onClose: () => void;
  onSaved: (shift: ShiftOut) => void;
}

export function ShiftForm({ shift, onClose, onSaved }: ShiftFormProps) {
  const editing = shift !== null;
  const [code, setCode] = useState(shift?.code ?? "");
  const [name, setName] = useState(shift?.name ?? "");
  const [startTime, setStartTime] = useState(shift?.start_time.slice(0, 5) ?? "");
  const [endTime, setEndTime] = useState(shift?.end_time.slice(0, 5) ?? "");
  const [graceMinutes, setGraceMinutes] = useState(String(shift?.grace_minutes ?? 0));
  const [isNight, setIsNight] = useState(shift?.is_night ?? false);

  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const codeRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    (input: ShiftIn | ShiftPatch) =>
      shift ? updateShift(shift.id, input as ShiftPatch) : createShift(input as ShiftIn),
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
    const grace = Number(graceMinutes);
    if (!editing) {
      void save.mutate({
        code: code.trim(),
        name: name.trim(),
        start_time: startTime,
        end_time: endTime,
        grace_minutes: grace,
        is_night: isNight,
      } satisfies ShiftIn);
      return;
    }
    void save.mutate({
      name: name.trim(),
      start_time: startTime,
      end_time: endTime,
      grace_minutes: grace,
      is_night: isNight,
    } satisfies ShiftPatch);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={editing ? `Edit ${shift.name}` : "New shift"}
      submitLabel={editing ? "Save changes" : "Create shift"}
      pendingLabel={editing ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={editing ? nameRef : codeRef}
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
        helper={editing ? "Immutable — a rename is a new shift." : "Letters, numbers, hyphens and underscores."}
        error={codeError ?? save.fieldErrors.code}
      />
      <TextField
        ref={nameRef}
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        error={nameError ?? save.fieldErrors.name}
      />
      <div className="grid grid-cols-2 gap-4">
        <TextField
          label="Start time"
          type="time"
          value={startTime}
          onChange={(e) => setStartTime(e.target.value)}
          error={save.fieldErrors.start_time}
        />
        <TextField
          label="End time"
          type="time"
          value={endTime}
          onChange={(e) => setEndTime(e.target.value)}
          error={save.fieldErrors.end_time}
        />
      </div>
      <TextField
        label="Grace period (minutes)"
        type="number"
        min={0}
        max={240}
        value={graceMinutes}
        onChange={(e) => setGraceMinutes(e.target.value)}
        error={save.fieldErrors.grace_minutes}
      />
      <Switch
        label="Night shift"
        description="Crosses midnight — attendance is grouped onto the day it started."
        checked={isNight}
        onChange={setIsNight}
      />
    </FormDialog>
  );
}
