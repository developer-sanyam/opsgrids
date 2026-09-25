import { useMemo, useRef, useState } from "react";

import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  FormDialog,
  RowActions,
  Select,
  Skeleton,
  Tag,
  TextArea,
  TextField,
  useToast,
} from "@opsgrids/ui";
import type { Column, TableSelection } from "@opsgrids/ui";

import { createRole, deleteRole, listPermissions, listRoles, updateRole } from "@/api";
import type { Grant, Permission, Role, RoleWrite, Scope } from "@/api/types";
import { downloadCsv } from "@/lib/csv";
import { PageHeader } from "@/ui";

const WRITE_PERMISSION = "core.identity.role.write";
const SCOPE_LADDER: Scope[] = ["OWN", "TEAM", "LOCATION", "ORG"];

function scopeOptions(maxScope: Scope): { value: string; label: string }[] {
  const ceiling = SCOPE_LADDER.indexOf(maxScope);
  return SCOPE_LADDER.filter((_, i) => i <= ceiling).map((s) => ({ value: s, label: s }));
}

function groupPermissions(permissions: Permission[]): Map<string, Map<string, Permission[]>> {
  const byProduct = new Map<string, Map<string, Permission[]>>();
  for (const perm of permissions) {
    if (!byProduct.has(perm.product)) byProduct.set(perm.product, new Map());
    const byModule = byProduct.get(perm.product);
    if (!byModule) continue;
    if (!byModule.has(perm.module)) byModule.set(perm.module, []);
    byModule.get(perm.module)?.push(perm);
  }
  return byProduct;
}

export function RolesPage() {
  const toast = useToast();
  const canWrite = useCan(WRITE_PERMISSION, "ORG");
  const roles = useAsync(listRoles, []);
  const permissions = useAsync(listPermissions, []);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Role | null>(null);
  const [duplicating, setDuplicating] = useState<Role | null>(null);
  const [deleting, setDeleting] = useState<Role | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const remove = useMutation((role: Role) => deleteRole(role.id), {
    onSuccess: (_result, role) => {
      setDeleting(null);
      toast.success(`${role.name} deleted.`);
      roles.reload();
    },
  });

  if (roles.status === "loading" || permissions.status === "loading") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Administration" title="Roles" description="What each role may do, across every product your organisation runs." />
        <Skeleton className="h-80 w-full rounded-card" />
      </div>
    );
  }

  if (roles.status === "error") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Administration" title="Roles" description="What each role may do, across every product your organisation runs." />
        <ErrorState title="Couldn't load roles" message={problemMessage(roles.error)} onRetry={roles.reload} />
      </div>
    );
  }

  if (permissions.status === "error") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Administration" title="Roles" description="What each role may do, across every product your organisation runs." />
        <ErrorState title="Couldn't load the permission catalogue" message={problemMessage(permissions.error)} onRetry={permissions.reload} />
      </div>
    );
  }

  const roleRows = roles.data;

  const columns: Column<Role>[] = [
    { key: "name", header: "Name", mobile: "title", render: (r) => <span className="font-semibold text-ink">{r.name}</span> },
    { key: "code", header: "Code", render: (r) => <span className="tnum text-xs text-faint">{r.code}</span> },
    { key: "product", header: "Product", render: (r) => r.product ?? "Any" },
    {
      key: "kind",
      header: "Kind",
      mobile: "status",
      render: (r) => (
        <div className="flex gap-1">
          {r.is_system ? <Tag tone="info">System</Tag> : <Tag tone="neutral">Custom</Tag>}
          {r.is_default ? <Tag tone="positive">Default</Tag> : null}
        </div>
      ),
    },
    { key: "grants", header: "Grants", cellClassName: "tnum", render: (r) => String(r.grants.length) },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (r) => (
        <RowActions
          label={r.name}
          actions={[
            { label: r.is_system ? "View" : "Edit", onSelect: () => setEditing(r) },
            { label: "Duplicate", onSelect: () => setDuplicating(r), hidden: !canWrite },
            { label: "Delete", tone: "danger", onSelect: () => setDeleting(r), hidden: !canWrite || r.is_system },
          ]}
        />
      ),
    },
  ];

  const roleSelection: TableSelection<Role> = {
    selected,
    onToggle: (key, next) =>
      setSelected((prev) => {
        const nextSet = new Set(prev);
        if (next) nextSet.add(key);
        else nextSet.delete(key);
        return nextSet;
      }),
    onToggleAll: (next) => setSelected(next ? new Set(roleRows.map((r) => r.id)) : new Set()),
    label: (r) => r.name,
  };

  function exportSelectedRoles() {
    const chosen = roleRows.filter((r) => selected.has(r.id));
    downloadCsv(
      "roles.csv",
      ["Name", "Code", "Product", "Kind", "Grants"],
      chosen.map((r) => [r.name, r.code, r.product ?? "Any", r.is_system ? "System" : "Custom", String(r.grants.length)]),
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Administration"
        title="Roles"
        description="What each role may do, across every product your organisation runs."
        actions={canWrite ? <Button onClick={() => setCreating(true)}>New role</Button> : null}
      />

      <Card className="p-0">
        <DataTable
          rows={roleRows}
          rowKey={(r) => r.id}
          columns={columns}
          mobileCard="auto"
          selection={roleSelection}
          bulkActions={
            <Button variant="outline" size="sm" onClick={exportSelectedRoles}>
              Export selected
            </Button>
          }
          emptyState={
            <EmptyState
              title="No roles yet"
              description="Every organisation starts with the owner's role; create a custom one for anything narrower."
              action={canWrite ? <Button onClick={() => setCreating(true)}>New role</Button> : null}
            />
          }
        />
      </Card>

      {creating ? (
        <RoleForm
          role={null}
          permissions={permissions.data}
          onClose={() => setCreating(false)}
          onSaved={(r) => {
            toast.success(`${r.name} created.`);
            roles.reload();
          }}
        />
      ) : null}

      {duplicating ? (
        <RoleForm
          role={null}
          seedFrom={duplicating}
          permissions={permissions.data}
          onClose={() => setDuplicating(null)}
          onSaved={(r) => {
            toast.success(`${r.name} created.`);
            roles.reload();
          }}
        />
      ) : null}

      {editing ? (
        <RoleForm
          role={editing}
          readOnly={editing.is_system}
          permissions={permissions.data}
          onClose={() => setEditing(null)}
          onSaved={(r) => {
            toast.success(`${r.name} saved.`);
            roles.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          remove.reset();
        }}
        onConfirm={() => deleting && void remove.mutate(deleting)}
        title={`Delete ${deleting?.name ?? "this role"}?`}
        body="Anyone holding it loses these grants immediately. This can't be undone."
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}

function RoleForm({
  role,
  seedFrom,
  readOnly = false,
  permissions,
  onClose,
  onSaved,
}: {
  role: Role | null;
  seedFrom?: Role;
  readOnly?: boolean;
  permissions: Permission[];
  onClose: () => void;
  onSaved: (role: Role) => void;
}) {
  const source = role ?? seedFrom ?? null;
  const [name, setName] = useState(source?.name ?? "");
  const [code, setCode] = useState(role ? role.code : seedFrom ? `${seedFrom.code}_COPY` : "");
  const [description, setDescription] = useState(source?.description ?? "");
  const [grants, setGrants] = useState<Map<string, Scope>>(
    new Map((source?.grants ?? []).map((g) => [g.permission_code, g.scope])),
  );
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  const grouped = useMemo(() => groupPermissions(permissions), [permissions]);

  const save = useMutation(
    (payload: RoleWrite) => (role ? updateRole(role.id, payload) : createRole(payload)),
    { onSuccess: (result) => { onClose(); onSaved(result); } },
  );

  function toggle(code_: string, checked: boolean, defaultScope: Scope) {
    setGrants((prev) => {
      const next = new Map(prev);
      if (checked) next.set(code_, defaultScope);
      else next.delete(code_);
      return next;
    });
  }

  function setScope(code_: string, scope: Scope) {
    setGrants((prev) => new Map(prev).set(code_, scope));
  }

  function submit() {
    setNameError(undefined);
    setCodeError(undefined);
    let invalid = false;
    if (name.trim().length < 1) {
      setNameError("Give the role a name.");
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
    const payload: RoleWrite = {
      code: code.trim(),
      name: name.trim(),
      description: description.trim() || null,
      grants: Array.from(grants.entries()).map(([permission_code, scope]): Grant => ({ permission_code, scope })),
    };
    void save.mutate(payload);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={readOnly ? onClose : submit}
      title={readOnly ? (role?.name ?? "Role") : role ? `Edit ${role.name}` : "New role"}
      description={readOnly ? "System roles cannot be edited or deleted — duplicate it to make a custom copy." : undefined}
      submitLabel={readOnly ? "Close" : role ? "Save changes" : "Create role"}
      pendingLabel="Saving…"
      pending={save.pending}
      size="lg"
      initialFocusRef={nameRef}
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? { error: problemMessage(save.error) }
        : {})}
    >
      {readOnly ? (
        <div className="flex flex-col gap-1">
          <p className="text-sm font-semibold text-ink">{role?.name}</p>
          <p className="text-xs text-faint tnum">{role?.code}</p>
        </div>
      ) : (
        <>
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
            onChange={(e) => setCode(e.target.value.toUpperCase().replace(/\s+/g, "_"))}
            helper={role ? "Cannot be changed." : "Cannot be changed once created."}
            disabled={role !== null}
            error={codeError ?? save.fieldErrors.code}
          />
          <TextArea label="Description" optional value={description} onChange={(e) => setDescription(e.target.value)} />
        </>
      )}

      <div className="flex flex-col gap-4">
        {Array.from(grouped.entries()).map(([product, modules]) => (
          <div key={product} className="flex flex-col gap-2">
            <h3 className="text-sm font-bold uppercase tracking-wide text-faint">{product}</h3>
            {Array.from(modules.entries()).map(([module, perms]) => (
              <div key={module} className="flex flex-col gap-1 rounded-tile border border-line p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted">{module}</p>
                {perms.map((perm) => {
                  const checked = grants.has(perm.code);
                  const scope = grants.get(perm.code) ?? perm.default_scope;
                  return (
                    <div key={perm.code} className="flex flex-wrap items-start justify-between gap-3 py-1.5">
                      <label className="flex min-w-0 items-start gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={readOnly}
                          onChange={(e) => toggle(perm.code, e.target.checked, perm.default_scope)}
                          className="mt-0.5 size-4 accent-accent"
                        />
                        <span className="flex flex-col">
                          <span className="font-medium text-ink">
                            {perm.resource}.{perm.action}
                            {perm.is_dangerous ? <span className="ml-1 text-xs font-semibold text-negative">(sensitive)</span> : null}
                          </span>
                          {perm.description ? <span className="text-xs text-muted">{perm.description}</span> : null}
                        </span>
                      </label>
                      {checked ? (
                        <Select
                          label={`Scope for ${perm.code}`}
                          hideLabel
                          containerClassName="w-32 shrink-0"
                          options={scopeOptions(perm.max_scope)}
                          value={scope}
                          disabled={readOnly}
                          onChange={(e) => setScope(perm.code, e.target.value as Scope)}
                        />
                      ) : null}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        ))}
      </div>
    </FormDialog>
  );
}
