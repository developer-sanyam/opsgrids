/**
 * `POST /attendance/check-in` — self-service, own record only. A field punch at a fenced location
 * needs a coordinate, checked server-side; this dialog always attempts to read one when the work
 * mode is `field`, but never blocks the submit on it — the server is the one place that knows
 * whether the chosen location actually has a fence.
 */
import { useEffect, useState } from "react";

import { problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, InlineAlert, Select, TextArea } from "@opsgrids/ui";

import { checkIn, listShifts } from "@/api/client";
import type { AttendanceOut, WorkMode } from "@/api/types";
import { useZoneLookup } from "@/lib/zone";

const WORK_MODE_OPTIONS: { value: WorkMode; label: string }[] = [
  { value: "office", label: "Office" },
  { value: "field", label: "Field" },
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
];

interface CheckInDialogProps {
  onClose: () => void;
  onSaved: (record: AttendanceOut) => void;
}

export function CheckInDialog({ onClose, onSaved }: CheckInDialogProps) {
  const zoneLookup = useZoneLookup();
  const [workMode, setWorkMode] = useState<WorkMode>("office");
  const [locationCode, setLocationCode] = useState("");
  const [shiftId, setShiftId] = useState("");
  const [shiftOptions, setShiftOptions] = useState<{ value: string; label: string }[]>([]);
  const [notes, setNotes] = useState("");
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(null);
  const [locationDenied, setLocationDenied] = useState(false);

  useEffect(() => {
    let active = true;
    listShifts({ size: 100 }).then((page) => {
      if (active) setShiftOptions(page.items.map((s) => ({ value: s.id, label: `${s.name} (${s.code})` })));
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (workMode !== "field" || coords || !("geolocation" in navigator)) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => setCoords({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      () => setLocationDenied(true),
      { timeout: 5000 },
    );
  }, [workMode, coords]);

  const save = useMutation(
    () =>
      checkIn({
        work_mode: workMode,
        source: "web",
        location_code: locationCode || null,
        shift_id: shiftId || null,
        notes: notes.trim() || null,
        ...(coords ? { lat: coords.lat, lng: coords.lng } : {}),
      }),
    { onSuccess: (saved) => { onClose(); onSaved(saved); } },
  );

  const locationOptions = [
    { value: "", label: "No location" },
    ...(zoneLookup
      ? Object.keys(zoneLookup.locations).map((code) => ({
          value: code,
          label: zoneLookup.names[code] ?? code,
        }))
      : []),
  ];

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={() => void save.mutate(undefined)}
      title="Check in"
      submitLabel="Check in"
      pendingLabel="Checking in…"
      pending={save.pending}
      {...(save.error ? { error: problemMessage(save.error) } : {})}
    >
      <Select
        label="Work mode"
        value={workMode}
        onChange={(e) => setWorkMode(e.target.value as WorkMode)}
        options={WORK_MODE_OPTIONS}
        error={save.fieldErrors.work_mode}
      />
      <Select
        label="Location"
        value={locationCode}
        onChange={(e) => setLocationCode(e.target.value)}
        options={locationOptions}
        error={save.fieldErrors.location_code}
      />
      <Select
        label="Shift"
        value={shiftId}
        onChange={(e) => setShiftId(e.target.value)}
        options={[{ value: "", label: "No shift" }, ...shiftOptions]}
        error={save.fieldErrors.shift_id}
      />
      <TextArea
        label="Notes"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        maxLength={500}
        counterFrom={400}
        optional
        error={save.fieldErrors.notes}
      />
      {workMode === "field" && locationDenied ? (
        <InlineAlert tone="warning" title="Location wasn't shared">
          A field punch at a fenced location needs it — allow location access and try again if
          the check-in is refused.
        </InlineAlert>
      ) : null}
    </FormDialog>
  );
}
