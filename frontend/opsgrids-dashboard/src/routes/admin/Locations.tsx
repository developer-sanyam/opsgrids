import { useRef, useState } from "react";

import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  FormDialog,
  RowActions,
  Select,
  Skeleton,
  Switch,
  Tag,
  TextField,
  useToast,
} from "@opsgrids/ui";
import type { Column } from "@opsgrids/ui";

import { createLocation, listLocations, updateLocation } from "@/api";
import type { Location, LocationCreate, LocationPatch } from "@/api/types";
import { COUNTRIES, TIMEZONES } from "@/lib/geo";
import { PageHeader } from "@/ui";

const WRITE_PERMISSION = "core.org.location.write";

export function LocationsPage() {
  const toast = useToast();
  const locations = useAsync(listLocations, []);
  const canWrite = useCan(WRITE_PERMISSION, "ORG");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Location | null>(null);

  if (locations.status === "loading") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Administration" title="Locations" description="The sites your organisation operates from." />
        <Skeleton className="h-72 w-full rounded-card" />
      </div>
    );
  }

  if (locations.status === "error") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Administration" title="Locations" description="The sites your organisation operates from." />
        <ErrorState title="Couldn't load locations" message={problemMessage(locations.error)} onRetry={locations.reload} />
      </div>
    );
  }

  const columns: Column<Location>[] = [
    { key: "name", header: "Name", render: (l) => <span className="font-semibold text-ink">{l.name}</span> },
    { key: "code", header: "Code", render: (l) => <span className="tnum text-xs text-faint">{l.code}</span> },
    { key: "timezone", header: "Timezone", render: (l) => l.timezone },
    { key: "country", header: "Country", render: (l) => l.country ?? "—" },
    {
      key: "status",
      header: "Status",
      render: (l) => (l.is_active ? <Tag tone="positive">Active</Tag> : <Tag tone="negative">Inactive</Tag>),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (l) => (
        <RowActions
          label={l.name}
          actions={[{ label: "Edit", onSelect: () => setEditing(l), hidden: !canWrite }]}
        />
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Administration"
        title="Locations"
        description="The sites your organisation operates from."
        actions={canWrite ? <Button onClick={() => setCreating(true)}>New location</Button> : null}
      />

      <Card className="p-0">
        <DataTable
          rows={locations.data}
          rowKey={(l) => l.id}
          columns={columns}
          emptyState={
            <EmptyState
              title="No locations yet"
              description="Create the first one — HRMS and other products reference locations by code."
              action={canWrite ? <Button onClick={() => setCreating(true)}>New location</Button> : null}
            />
          }
        />
      </Card>

      {creating ? (
        <LocationForm
          location={null}
          onClose={() => setCreating(false)}
          onSaved={(loc) => {
            toast.success(`${loc.name} created.`);
            locations.reload();
          }}
        />
      ) : null}
      {editing ? (
        <LocationForm
          location={editing}
          onClose={() => setEditing(null)}
          onSaved={(loc) => {
            toast.success(`${loc.name} saved.`);
            locations.reload();
          }}
        />
      ) : null}
    </div>
  );
}

function LocationForm({
  location,
  onClose,
  onSaved,
}: {
  location: Location | null;
  onClose: () => void;
  onSaved: (location: Location) => void;
}) {
  const [name, setName] = useState(location?.name ?? "");
  const [code, setCode] = useState(location?.code ?? "");
  const [timezone, setTimezone] = useState(location?.timezone ?? "Asia/Kolkata");
  const [country, setCountry] = useState(location?.country ?? "");
  const [line1, setLine1] = useState(String(location?.address?.line1 ?? ""));
  const [city, setCity] = useState(String(location?.address?.city ?? ""));
  const [isActive, setIsActive] = useState(location?.is_active ?? true);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  // Compared against the same seed the fields above initialised from — this form unmounts on
  // close (FormDialog's resetting-is-structural note), so `location` never changes under it.
  const dirty =
    name !== (location?.name ?? "") ||
    code !== (location?.code ?? "") ||
    timezone !== (location?.timezone ?? "Asia/Kolkata") ||
    country !== (location?.country ?? "") ||
    line1 !== String(location?.address?.line1 ?? "") ||
    city !== String(location?.address?.city ?? "") ||
    isActive !== (location?.is_active ?? true);

  const save = useMutation(
    (payload: LocationCreate | LocationPatch) =>
      location ? updateLocation(location.id, payload as LocationPatch) : createLocation(payload as LocationCreate),
    { onSuccess: (result) => { onClose(); onSaved(result); } },
  );

  function submit() {
    setNameError(undefined);
    setCodeError(undefined);
    let invalid = false;
    if (name.trim().length < 1) {
      setNameError("Give the location a name.");
      invalid = true;
    }
    if (!location && !/^[A-Za-z0-9_-]+$/.test(code.trim())) {
      setCodeError("Letters, digits, underscores and hyphens only.");
      invalid = true;
    }
    if (invalid) {
      nameRef.current?.focus();
      return;
    }
    const address = line1.trim() || city.trim() ? { line1: line1.trim(), city: city.trim() } : undefined;
    if (location) {
      const patch: LocationPatch = { name: name.trim(), timezone, is_active: isActive };
      if (country) patch.country = country;
      if (address) patch.address = address;
      void save.mutate(patch);
    } else {
      const payload: LocationCreate = { name: name.trim(), code: code.trim(), timezone };
      if (country) payload.country = country;
      if (address) payload.address = address;
      void save.mutate(payload);
    }
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={location ? `Edit ${location.name}` : "New location"}
      submitLabel={location ? "Save changes" : "Create location"}
      pendingLabel={location ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={nameRef}
      dirty={dirty}
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? { error: problemMessage(save.error) }
        : {})}
    >
      <TextField
        ref={nameRef}
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        error={nameError ?? save.fieldErrors.name}
      />
      {location ? (
        <TextField label="Code" value={location.code} disabled helper="Cannot be changed." />
      ) : (
        <TextField
          label="Code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          helper="Cannot be changed once created."
          required
          error={codeError ?? save.fieldErrors.code}
        />
      )}
      <Select label="Timezone" options={TIMEZONES} value={timezone} onChange={(e) => setTimezone(e.target.value)} />
      <Select
        label="Country"
        placeholder="Choose a country"
        options={COUNTRIES}
        value={country}
        onChange={(e) => setCountry(e.target.value)}
      />
      <TextField label="Address line" optional value={line1} onChange={(e) => setLine1(e.target.value)} />
      <TextField label="City" optional value={city} onChange={(e) => setCity(e.target.value)} />
      {location ? (
        <Switch
          checked={isActive}
          onChange={setIsActive}
          label="Active"
          description={
            isActive
              ? "Deactivating is refused while members still reference this location."
              : "Deactivated locations stay visible but are excluded from active listings."
          }
        />
      ) : null}
    </FormDialog>
  );
}
