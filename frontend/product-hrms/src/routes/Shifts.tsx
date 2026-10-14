import { useState } from "react";

import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  ClockIcon,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  MapPinIcon,
  PageHeader,
  RowActions,
  SectionHeader,
  Skeleton,
  Tag,
  useToast,
} from "@opsgrids/ui";
import type { Column } from "@opsgrids/ui";

import { deleteGeofence, deleteShift, listGeofences, listShifts } from "@/api/client";
import type { GeofenceOut, ShiftOut } from "@/api/types";
import { GeofenceForm } from "@/ui/GeofenceForm";
import { ShiftForm } from "@/ui/ShiftForm";

function ShiftsSection() {
  const toast = useToast();
  const canWrite = useCan("hrms.time.shift.write", "ORG");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<ShiftOut | null>(null);
  const [deleting, setDeleting] = useState<ShiftOut | null>(null);

  const shifts = useAsync(() => listShifts({ size: 100 }), []);

  const remove = useMutation((shift: ShiftOut) => deleteShift(shift.id), {
    onSuccess: (_result, shift) => {
      setDeleting(null);
      toast.success(`${shift.name} retired.`);
      shifts.reload();
    },
  });

  const columns: Column<ShiftOut>[] = [
    {
      key: "code",
      header: "Code",
      mobile: "field",
      render: (s) => <span className="tnum font-semibold">{s.code}</span>,
    },
    { key: "name", header: "Name", mobile: "title", render: (s) => s.name },
    {
      key: "hours",
      header: "Hours",
      render: (s) => (
        <span className="tnum text-muted">
          {s.start_time.slice(0, 5)}–{s.end_time.slice(0, 5)}
          {s.is_night ? <Tag className="ml-2">Night</Tag> : null}
        </span>
      ),
    },
    { key: "grace", header: "Grace", align: "right", render: (s) => <span className="tnum">{s.grace_minutes}m</span> },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (s) => (
        <RowActions
          label={s.name}
          inlineLimit={1}
          className="whitespace-nowrap"
          actions={[
            { label: "Edit", onSelect: () => setEditing(s), hidden: !canWrite },
            { label: "Delete", tone: "danger", onSelect: () => setDeleting(s), hidden: !canWrite },
          ]}
        />
      ),
    },
  ];

  return (
    <Card className="p-4 sm:p-6 flex flex-col gap-4">
      <SectionHeader
        title="Shifts"
        subtitle="Working hours templates for check-in and check-out."
        action={
          <Can permission="hrms.time.shift.write" scope="ORG">
            <Button size="sm" onClick={() => setCreating(true)}>New shift</Button>
          </Can>
        }
      />

      {shifts.status === "loading" ? (
        <div className="flex flex-col gap-3 py-2">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-12 w-full rounded-tile" />)}
        </div>
      ) : null}
      {shifts.status === "error" ? (
        <ErrorState title="Couldn't load shifts" message={problemMessage(shifts.error)} onRetry={shifts.reload} />
      ) : null}
      {shifts.status === "success" ? (
        <DataTable
          columns={columns}
          rows={shifts.data.items}
          rowKey={(s) => s.id}
          caption="Shifts"
          mobileCard="auto"
          emptyState={
            <EmptyState
              icon={<ClockIcon className="size-6" />}
              title="No shifts yet"
              description="Create the first one to assign at check-in."
              action={
                <Can permission="hrms.time.shift.write" scope="ORG">
                  <Button size="sm" onClick={() => setCreating(true)}>New shift</Button>
                </Can>
              }
            />
          }
        />
      ) : null}

      {creating ? (
        <ShiftForm
          shift={null}
          onClose={() => setCreating(false)}
          onSaved={(s) => { toast.success(`${s.name} created.`); shifts.reload(); }}
        />
      ) : null}
      {editing ? (
        <ShiftForm
          shift={editing}
          onClose={() => setEditing(null)}
          onSaved={(s) => { toast.success(`${s.name} saved.`); shifts.reload(); }}
        />
      ) : null}
      <ConfirmDialog
        open={deleting !== null}
        onClose={() => { setDeleting(null); remove.reset(); }}
        onConfirm={() => deleting && void remove.mutate(deleting)}
        title="Retire this shift?"
        body={`${deleting?.name} (${deleting?.code}) is retired. It can no longer be assigned at check-in.`}
        confirmLabel="Retire shift"
        pendingLabel="Retiring…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </Card>
  );
}

function GeofencesSection() {
  const toast = useToast();
  const canWrite = useCan("hrms.time.shift.write", "ORG");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<GeofenceOut | null>(null);
  const [deleting, setDeleting] = useState<GeofenceOut | null>(null);

  const geofences = useAsync(() => listGeofences({ size: 100 }), []);

  const remove = useMutation((g: GeofenceOut) => deleteGeofence(g.id), {
    onSuccess: (_result, g) => {
      setDeleting(null);
      toast.success(`${g.name} removed.`);
      geofences.reload();
    },
  });

  const columns: Column<GeofenceOut>[] = [
    {
      key: "location",
      header: "Location",
      mobile: "field",
      render: (g) => <span className="tnum font-semibold">{g.location_code}</span>,
    },
    { key: "name", header: "Name", mobile: "title", render: (g) => g.name },
    {
      key: "center",
      header: "Center",
      render: (g) => <span className="tnum text-muted">{g.center_lat}, {g.center_lng}</span>,
    },
    { key: "radius", header: "Radius", align: "right", render: (g) => <span className="tnum">{g.radius_m} m</span> },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (g) => (
        <RowActions
          label={g.name}
          inlineLimit={1}
          className="whitespace-nowrap"
          actions={[
            { label: "Edit", onSelect: () => setEditing(g), hidden: !canWrite },
            { label: "Delete", tone: "danger", onSelect: () => setDeleting(g), hidden: !canWrite },
          ]}
        />
      ),
    },
  ];

  return (
    <Card className="p-4 sm:p-6 flex flex-col gap-4">
      <SectionHeader
        title="Geo-fences"
        subtitle="Where a field check-in must be inside to be accepted."
        action={
          <Can permission="hrms.time.shift.write" scope="ORG">
            <Button size="sm" onClick={() => setCreating(true)}>New geo-fence</Button>
          </Can>
        }
      />

      {geofences.status === "loading" ? (
        <div className="flex flex-col gap-3 py-2">
          {Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-12 w-full rounded-tile" />)}
        </div>
      ) : null}
      {geofences.status === "error" ? (
        <ErrorState
          title="Couldn't load geo-fences"
          message={problemMessage(geofences.error)}
          onRetry={geofences.reload}
        />
      ) : null}
      {geofences.status === "success" ? (
        <DataTable
          columns={columns}
          rows={geofences.data.items}
          rowKey={(g) => g.id}
          caption="Geo-fences"
          mobileCard="auto"
          emptyState={
            <EmptyState
              icon={<MapPinIcon className="size-6" />}
              title="No geo-fences yet"
              description="Field check-ins are accepted anywhere until a location has one."
              action={
                <Can permission="hrms.time.shift.write" scope="ORG">
                  <Button size="sm" onClick={() => setCreating(true)}>New geo-fence</Button>
                </Can>
              }
            />
          }
        />
      ) : null}

      {creating ? (
        <GeofenceForm
          geofence={null}
          onClose={() => setCreating(false)}
          onSaved={(g) => { toast.success(`${g.name} created.`); geofences.reload(); }}
        />
      ) : null}
      {editing ? (
        <GeofenceForm
          geofence={editing}
          onClose={() => setEditing(null)}
          onSaved={(g) => { toast.success(`${g.name} saved.`); geofences.reload(); }}
        />
      ) : null}
      <ConfirmDialog
        open={deleting !== null}
        onClose={() => { setDeleting(null); remove.reset(); }}
        onConfirm={() => deleting && void remove.mutate(deleting)}
        title="Remove this geo-fence?"
        body={`${deleting?.name} (${deleting?.location_code}) is removed. Field check-ins there will no longer be fenced.`}
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </Card>
  );
}

export function ShiftsPage() {
  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader eyebrow="Records" title="Shifts & geo-fences" />
      <ShiftsSection />
      <GeofencesSection />
    </div>
  );
}
