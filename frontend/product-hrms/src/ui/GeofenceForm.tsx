import { useRef, useState } from "react";

import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Select, TextField } from "@opsgrids/ui";

import { createGeofence, updateGeofence } from "@/api/client";
import type { GeofenceIn, GeofenceOut, GeofencePatch } from "@/api/types";
import { useZoneLookup } from "@/lib/zone";

interface GeofenceFormProps {
  geofence: GeofenceOut | null;
  onClose: () => void;
  onSaved: (geofence: GeofenceOut) => void;
}

/** Coordinates are sent and displayed as strings throughout — never parsed through a float, which
 *  is how a fence boundary loses the precision the server stores it at (`Decimal`). */
export function GeofenceForm({ geofence, onClose, onSaved }: GeofenceFormProps) {
  const editing = geofence !== null;
  const zoneLookup = useZoneLookup();
  const [locationCode, setLocationCode] = useState(geofence?.location_code ?? "");
  const [name, setName] = useState(geofence?.name ?? "");
  const [centerLat, setCenterLat] = useState(geofence?.center_lat ?? "");
  const [centerLng, setCenterLng] = useState(geofence?.center_lng ?? "");
  const [radiusM, setRadiusM] = useState(String(geofence?.radius_m ?? 100));

  const [locationError, setLocationError] = useState<string | undefined>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  // Compared against the same seed the fields above initialised from — this form unmounts on
  // close (FormDialog's resetting-is-structural note), so `geofence` never changes under it.
  const dirty =
    locationCode !== (geofence?.location_code ?? "") ||
    name !== (geofence?.name ?? "") ||
    centerLat !== (geofence?.center_lat ?? "") ||
    centerLng !== (geofence?.center_lng ?? "") ||
    radiusM !== String(geofence?.radius_m ?? 100);

  const save = useMutation(
    (input: GeofenceIn | GeofencePatch) =>
      geofence ? updateGeofence(geofence.id, input as GeofencePatch) : createGeofence(input as GeofenceIn),
    { onSuccess: (saved) => { onClose(); onSaved(saved); } },
  );

  function submit() {
    setLocationError(undefined);
    setNameError(undefined);
    if (!editing && !locationCode) {
      setLocationError("Choose a location.");
      return;
    }
    if (name.trim().length < 1) {
      setNameError("Name is required.");
      nameRef.current?.focus();
      return;
    }
    if (!editing) {
      void save.mutate({
        location_code: locationCode,
        name: name.trim(),
        center_lat: centerLat,
        center_lng: centerLng,
        radius_m: Number(radiusM),
      } satisfies GeofenceIn);
      return;
    }
    void save.mutate({
      name: name.trim(),
      center_lat: centerLat,
      center_lng: centerLng,
      radius_m: Number(radiusM),
    } satisfies GeofencePatch);
  }

  const locationOptions = zoneLookup
    ? Object.keys(zoneLookup.locations).map((code) => ({
        value: code,
        label: zoneLookup.names[code] ?? code,
      }))
    : [];

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={editing ? `Edit ${geofence.name}` : "New geo-fence"}
      submitLabel={editing ? "Save changes" : "Create geo-fence"}
      pendingLabel={editing ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={nameRef}
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
      {editing ? (
        <TextField label="Location" value={geofence.location_code} disabled helper="Immutable — the punch match key." />
      ) : (
        <Select
          label="Location"
          value={locationCode}
          onChange={(e) => setLocationCode(e.target.value)}
          options={locationOptions}
          placeholder="Choose a location…"
          required
          error={locationError ?? save.fieldErrors.location_code}
        />
      )}
      <TextField
        ref={nameRef}
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        error={nameError ?? save.fieldErrors.name}
      />
      <div className="grid grid-cols-2 gap-4">
        <TextField
          label="Center latitude"
          value={centerLat}
          onChange={(e) => setCenterLat(e.target.value)}
          helper="Decimal degrees, e.g. 12.9716"
          required
          error={save.fieldErrors.center_lat}
        />
        <TextField
          label="Center longitude"
          value={centerLng}
          onChange={(e) => setCenterLng(e.target.value)}
          helper="Decimal degrees, e.g. 77.5946"
          required
          error={save.fieldErrors.center_lng}
        />
      </div>
      <TextField
        label="Radius (metres)"
        type="number"
        min={1}
        max={100000}
        value={radiusM}
        onChange={(e) => setRadiusM(e.target.value)}
        required
        error={save.fieldErrors.radius_m}
      />
    </FormDialog>
  );
}
