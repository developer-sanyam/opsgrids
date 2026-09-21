import { useRef, useState } from "react";

import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  Combobox,
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
import type { ComboboxOption } from "@opsgrids/ui";

import { createOrgUnit, getMember, listMembers, listOrgUnits, moveOrgUnit, updateOrgUnit } from "@/api";
import type { OrgUnit, OrgUnitCreate, OrgUnitPatch } from "@/api/types";
import { descendantIds, flattenOrgUnits } from "@/lib/orgUnits";
import { PageHeader } from "@/ui";

const WRITE_PERMISSION = "core.org.unit.write";
const KINDS = [
  { value: "department", label: "Department" },
  { value: "division", label: "Division" },
  { value: "team", label: "Team" },
];

export function OrgUnitsPage() {
  const toast = useToast();
  const units = useAsync(listOrgUnits, []);
  const canWrite = useCan(WRITE_PERMISSION, "ORG");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<OrgUnit | null>(null);
  const [moving, setMoving] = useState<OrgUnit | null>(null);

  if (units.status === "loading") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Administration" title="Org units" description="The department tree your organisation is structured into." />
        <Skeleton className="h-80 w-full rounded-card" />
      </div>
    );
  }

  if (units.status === "error") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Administration" title="Org units" description="The department tree your organisation is structured into." />
        <ErrorState title="Couldn't load org units" message={problemMessage(units.error)} onRetry={units.reload} />
      </div>
    );
  }

  const flat = flattenOrgUnits(units.data);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Administration"
        title="Org units"
        description="The department tree your organisation is structured into."
        actions={
          canWrite ? (
            <Button onClick={() => setCreating(true)}>New department</Button>
          ) : null
        }
      />

      {flat.length === 0 ? (
        <EmptyState
          title="No departments yet"
          description="Create the first one to start structuring your organisation."
          action={canWrite ? <Button onClick={() => setCreating(true)}>New department</Button> : null}
        />
      ) : (
        <Card className="p-2">
          <ul role="tree" aria-label="Departments" className="flex list-none flex-col gap-0.5 p-0">
            {flat.map(({ unit, depth }) => (
              <li key={unit.id} role="treeitem" aria-level={depth + 1} aria-selected={false}>
                <div
                  className="flex items-center justify-between gap-3 rounded-tile px-3 py-2.5 hover:bg-ground"
                  style={{ paddingLeft: `${depth * 20 + 12}px` }}
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm font-semibold text-ink">{unit.name}</span>
                    <span className="tnum text-xs text-faint">{unit.code}</span>
                    <Tag tone="neutral">{unit.kind}</Tag>
                    {!unit.is_active ? <Tag tone="negative">Inactive</Tag> : null}
                  </div>
                  <RowActions
                    label={unit.name}
                    actions={[
                      { label: "Edit", onSelect: () => setEditing(unit), hidden: !canWrite },
                      { label: "Move to…", onSelect: () => setMoving(unit), hidden: !canWrite },
                    ]}
                  />
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {creating ? (
        <CreateOrgUnitDialog
          units={units.data}
          onClose={() => setCreating(false)}
          onSaved={(unit) => {
            toast.success(`${unit.name} created.`);
            units.reload();
          }}
        />
      ) : null}

      {editing ? (
        <EditOrgUnitDialog
          unit={editing}
          onClose={() => setEditing(null)}
          onSaved={(unit) => {
            toast.success(`${unit.name} saved.`);
            units.reload();
          }}
        />
      ) : null}

      {moving ? (
        <MoveOrgUnitDialog
          unit={moving}
          units={units.data}
          onClose={() => setMoving(null)}
          onSaved={(unit) => {
            toast.success(`${unit.name} moved.`);
            units.reload();
          }}
        />
      ) : null}
    </div>
  );
}

function parentOptions(units: OrgUnit[], excludeIds: Set<string>): { value: string; label: string }[] {
  return flattenOrgUnits(units)
    .filter(({ unit }) => !excludeIds.has(unit.id))
    .map(({ unit, depth }) => ({ value: unit.id, label: `${"— ".repeat(depth)}${unit.name}` }));
}

function CreateOrgUnitDialog({
  units,
  onClose,
  onSaved,
}: {
  units: OrgUnit[];
  onClose: () => void;
  onSaved: (unit: OrgUnit) => void;
}) {
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [parentId, setParentId] = useState("");
  const [kind, setKind] = useState("department");
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation((payload: OrgUnitCreate) => createOrgUnit(payload), {
    onSuccess: (unit) => {
      onClose();
      onSaved(unit);
    },
  });

  function submit() {
    setNameError(undefined);
    setCodeError(undefined);
    let invalid = false;
    if (name.trim().length < 1) {
      setNameError("Give the department a name.");
      invalid = true;
    }
    if (!/^[A-Za-z0-9_-]+$/.test(code.trim())) {
      setCodeError("Letters, digits, underscores and hyphens only.");
      invalid = true;
    }
    if (invalid) {
      nameRef.current?.focus();
      return;
    }
    const payload: OrgUnitCreate = { name: name.trim(), code: code.trim(), kind };
    if (parentId) payload.parent_id = parentId;
    void save.mutate(payload);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="New department"
      submitLabel="Create department"
      pendingLabel="Creating…"
      pending={save.pending}
      initialFocusRef={nameRef}
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? { error: problemMessage(save.error) }
        : {})}
    >
      <TextField
        ref={nameRef}
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        error={nameError ?? save.fieldErrors.name}
      />
      <TextField
        label="Code"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        helper="Cannot be changed once created."
        error={codeError ?? save.fieldErrors.code}
      />
      <Select label="Kind" options={KINDS} value={kind} onChange={(e) => setKind(e.target.value)} />
      <Select
        label="Parent"
        placeholder="No parent — a root department"
        options={parentOptions(units, new Set())}
        value={parentId}
        onChange={(e) => setParentId(e.target.value)}
      />
    </FormDialog>
  );
}

function EditOrgUnitDialog({
  unit,
  onClose,
  onSaved,
}: {
  unit: OrgUnit;
  onClose: () => void;
  onSaved: (unit: OrgUnit) => void;
}) {
  const [name, setName] = useState(unit.name);
  const [kind, setKind] = useState(unit.kind);
  const [isActive, setIsActive] = useState(unit.is_active);
  const [head, setHead] = useState<ComboboxOption | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  useAsync(async () => {
    if (!unit.head_membership_id) return null;
    const member = await getMember(unit.head_membership_id);
    setHead({ id: member.id, label: member.display_name ?? member.full_name, sublabel: member.email });
    return member;
  }, [unit.head_membership_id]);

  const save = useMutation((patch: OrgUnitPatch) => updateOrgUnit(unit.id, patch), {
    onSuccess: (updated) => {
      onClose();
      onSaved(updated);
    },
  });

  function submit() {
    if (name.trim().length < 1) {
      nameRef.current?.focus();
      return;
    }
    const patch: OrgUnitPatch = { name: name.trim(), kind, is_active: isActive };
    patch.head_membership_id = head?.id ?? null;
    void save.mutate(patch);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Edit ${unit.name}`}
      submitLabel="Save changes"
      pendingLabel="Saving…"
      pending={save.pending}
      initialFocusRef={nameRef}
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? { error: problemMessage(save.error) }
        : {})}
    >
      <TextField
        ref={nameRef}
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        error={save.fieldErrors.name}
      />
      <TextField label="Code" value={unit.code} disabled helper="Cannot be changed." />
      <Select
        label="Kind"
        options={KINDS}
        value={kind}
        onChange={(e) => setKind(e.target.value)}
        error={save.fieldErrors.kind}
      />
      <Combobox
        label="Head of department"
        value={head}
        onChange={setHead}
        loadOptions={async (q, signal) => {
          const page = await listMembers({ q, size: 10 });
          if (signal.aborted) return [];
          return page.items.map((m) => ({ id: m.id, label: m.display_name ?? m.full_name, sublabel: m.email }));
        }}
        placeholder="Search people"
      />
      <Switch
        checked={isActive}
        onChange={setIsActive}
        label="Active"
        description={
          isActive
            ? "Deactivating is refused while this department has active children or members."
            : "Deactivated departments stay visible but are excluded from active listings."
        }
      />
    </FormDialog>
  );
}

function MoveOrgUnitDialog({
  unit,
  units,
  onClose,
  onSaved,
}: {
  unit: OrgUnit;
  units: OrgUnit[];
  onClose: () => void;
  onSaved: (unit: OrgUnit) => void;
}) {
  const [parentId, setParentId] = useState(unit.parent_id ?? "");
  const excluded = new Set(descendantIds(units, unit.id));
  excluded.add(unit.id);

  const save = useMutation((newParentId: string) => moveOrgUnit(unit.id, newParentId || null), {
    onSuccess: (updated) => {
      onClose();
      onSaved(updated);
    },
  });

  function submit() {
    void save.mutate(parentId);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Move ${unit.name}`}
      description="Re-parents this department immediately."
      submitLabel="Move"
      pendingLabel="Moving…"
      pending={save.pending}
      {...(save.error ? { error: problemMessage(save.error) } : {})}
    >
      <Select
        label="New parent"
        placeholder="No parent — make it a root department"
        options={parentOptions(units, excluded)}
        value={parentId}
        onChange={(e) => setParentId(e.target.value)}
      />
    </FormDialog>
  );
}
