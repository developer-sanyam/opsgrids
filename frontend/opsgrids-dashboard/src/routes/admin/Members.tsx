import { useEffect, useRef, useState } from "react";

import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  Combobox,
  ConfirmDialog,
  DataTable,
  Dialog,
  EmptyState,
  ErrorState,
  FormDialog,
  InlineAlert,
  Pagination,
  RowActions,
  SearchInput,
  Select,
  Skeleton,
  Tag,
  TextArea,
  TextField,
  useToast,
} from "@opsgrids/ui";
import type { Column, ComboboxOption } from "@opsgrids/ui";

import {
  createInvitation,
  getMember,
  listInvitations,
  listLocations,
  listMembers,
  listOrgUnits,
  listRoles,
  removeMember,
  revokeInvitation,
  setMemberRoles,
  updateMember,
} from "@/api";
import type { Invitation, InvitationCreate, InvitationCreated, Location, Member, MemberPatch, OrgUnit, Role } from "@/api/types";
import { formatDate } from "@/lib/money";
import { flattenOrgUnits } from "@/lib/orgUnits";
import { optionalProp } from "@/lib/props";
import { PageHeader } from "@/ui";

const WRITE_PERMISSION = "core.identity.membership.write";
const ROLE_WRITE_PERMISSION = "core.identity.role.write";

function departmentName(units: OrgUnit[], id: string | null): string {
  if (!id) return "—";
  const found = flattenOrgUnits(units).find((f) => f.unit.id === id);
  return found?.unit.name ?? "—";
}

export function MembersPage() {
  const toast = useToast();
  const canWrite = useCan(WRITE_PERMISSION, "ORG");
  const canAssignRoles = useCan(ROLE_WRITE_PERMISSION, "ORG");

  const [rawQuery, setRawQuery] = useState("");
  const [query, setQuery] = useState("");
  const [orgUnitId, setOrgUnitId] = useState("");
  const [status, setStatus] = useState("");
  const [linked, setLinked] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);

  useEffect(() => {
    const handle = setTimeout(() => setQuery(rawQuery), 300);
    return () => clearTimeout(handle);
  }, [rawQuery]);

  const units = useAsync(listOrgUnits, []);
  const locations = useAsync(listLocations, []);
  const roles = useAsync(listRoles, []);

  const members = useAsync(
    () =>
      listMembers({
        q: query || undefined,
        org_unit_id: orgUnitId || undefined,
        status: status || undefined,
        linked: linked === "" ? undefined : linked === "true",
        page,
        size,
      }),
    [query, orgUnitId, status, linked, page, size],
  );

  const [editing, setEditing] = useState<Member | null>(null);
  const [assigningRoles, setAssigningRoles] = useState<Member | null>(null);
  const [removing, setRemoving] = useState<Member | null>(null);
  const [removeReason, setRemoveReason] = useState("");
  const [inviting, setInviting] = useState(false);
  const [revoking, setRevoking] = useState<Invitation | null>(null);

  const remove = useMutation((member: Member) => removeMember(member.id, removeReason.trim() || undefined), {
    onSuccess: (_result, member) => {
      setRemoving(null);
      setRemoveReason("");
      toast.success(`${member.display_name ?? member.full_name} removed.`);
      members.reload();
    },
  });

  const revoke = useMutation((invitation: Invitation) => revokeInvitation(invitation.id), {
    onSuccess: (_result, invitation) => {
      setRevoking(null);
      toast.success(`Invitation to ${invitation.email} revoked.`);
      invitations.reload();
    },
  });

  const invitations = useAsync(listInvitations, []);

  const departments = units.status === "success" ? flattenOrgUnits(units.data) : [];

  const columns: Column<Member>[] = [
    {
      key: "name",
      header: "Name",
      render: (m) => (
        <div className="flex items-center gap-2">
          <span className="font-semibold text-ink">{m.display_name ?? m.full_name}</span>
          {m.is_owner ? <Tag tone="info">Owner</Tag> : null}
        </div>
      ),
    },
    { key: "email", header: "Email", render: (m) => <span className="text-ink-soft">{m.email}</span> },
    { key: "job_title", header: "Job title", render: (m) => m.job_title ?? "—" },
    { key: "department", header: "Department", render: (m) => (units.status === "success" ? departmentName(units.data, m.org_unit_id) : "—") },
    {
      key: "roles",
      header: "Roles",
      render: (m) => (
        <div className="flex flex-wrap gap-1">
          {m.roles.length === 0 ? <span className="text-xs text-faint">None</span> : m.roles.map((r) => <Tag key={r}>{r}</Tag>)}
        </div>
      ),
    },
    { key: "status", header: "Status", render: (m) => <Tag tone={m.status === "active" ? "positive" : "neutral"}>{m.status}</Tag> },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (m) => (
        <RowActions
          label={m.display_name ?? m.full_name}
          actions={[
            { label: "Edit", onSelect: () => setEditing(m), hidden: !canWrite },
            { label: "Manage roles", onSelect: () => setAssigningRoles(m), hidden: !canAssignRoles },
            {
              label: "Remove",
              tone: "danger",
              onSelect: () => setRemoving(m),
              // The owner cannot be removed — the service 422s and there is no ownership-transfer
              // endpoint to route them to instead, so the control is not offered at all rather
              // than offered and refused.
              hidden: !canWrite || m.is_owner,
            },
          ]}
        />
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="Administration"
        title="Members"
        description="Everyone who belongs to your organisation, their department, and the roles they hold."
        actions={
          canWrite ? <Button onClick={() => setInviting(true)}>Invite</Button> : null
        }
      />

      {members.status === "loading" ? <Skeleton className="h-80 w-full rounded-card" /> : null}
      {members.status === "error" ? (
        <ErrorState title="Couldn't load members" message={problemMessage(members.error)} onRetry={members.reload} />
      ) : null}

      {members.status === "success" ? (
        <>
          <Card className="p-0">
            <DataTable
              rows={members.data.items}
              rowKey={(m) => m.id}
              columns={columns}
              toolbar={
                <div className="flex flex-wrap items-center gap-3">
                  <SearchInput
                    label="Search members"
                    placeholder="Search by name, email or job title"
                    value={rawQuery}
                    onChange={(e) => {
                      setRawQuery(e.target.value);
                      setPage(1);
                    }}
                    containerClassName="min-w-[16rem]"
                  />
                  <Select
                    label="Department"
                    containerClassName="w-48"
                    placeholder="All departments"
                    options={departments.map(({ unit, depth }) => ({ value: unit.id, label: `${"— ".repeat(depth)}${unit.name}` }))}
                    value={orgUnitId}
                    onChange={(e) => {
                      setOrgUnitId(e.target.value);
                      setPage(1);
                    }}
                  />
                  <Select
                    label="Status"
                    containerClassName="w-36"
                    placeholder="All statuses"
                    options={[{ value: "active", label: "Active" }]}
                    value={status}
                    onChange={(e) => {
                      setStatus(e.target.value);
                      setPage(1);
                    }}
                  />
                  <Select
                    label="Employee link"
                    containerClassName="w-52"
                    placeholder="Linked or not"
                    options={[
                      { value: "true", label: "Linked to an employee" },
                      { value: "false", label: "Not linked to an employee" },
                    ]}
                    value={linked}
                    onChange={(e) => {
                      setLinked(e.target.value);
                      setPage(1);
                    }}
                  />
                </div>
              }
              emptyState={
                <EmptyState
                  title={query ? "No members match that search" : "No members yet"}
                  description={query ? "Try a shorter search term." : "Invite the first person to get started."}
                  action={
                    query ? (
                      <Button variant="outline" onClick={() => setRawQuery("")}>
                        Clear search
                      </Button>
                    ) : canWrite ? (
                      <Button onClick={() => setInviting(true)}>Invite</Button>
                    ) : null
                  }
                />
              }
            />
          </Card>
          <Pagination
            page={members.data.page}
            pages={members.data.pages}
            total={members.data.total}
            size={members.data.size}
            noun="members"
            onPageChange={setPage}
            onSizeChange={(n) => {
              setSize(n);
              setPage(1);
            }}
          />
        </>
      ) : null}

      <section className="flex flex-col gap-4">
        <h2 className="text-lg font-bold tracking-tight">Pending invitations</h2>
        {invitations.status === "loading" ? <Skeleton className="h-32 w-full rounded-card" /> : null}
        {invitations.status === "error" ? (
          <ErrorState title="Couldn't load invitations" message={problemMessage(invitations.error)} onRetry={invitations.reload} />
        ) : null}
        {invitations.status === "success" && invitations.data.length === 0 ? (
          <p className="text-sm text-muted">No invitations are waiting to be accepted.</p>
        ) : null}
        {invitations.status === "success" && invitations.data.length > 0 ? (
          <Card className="p-0">
            <table className="w-full min-w-[40rem] text-left text-sm">
              <thead>
                <tr className="text-xs uppercase tracking-wide text-muted">
                  <th className="px-5 py-3 font-semibold">Email</th>
                  <th className="px-5 py-3 font-semibold">Roles</th>
                  <th className="px-5 py-3 font-semibold">Expires</th>
                  <th className="px-5 py-3 font-semibold">Created</th>
                  <th className="px-5 py-3 text-right font-semibold">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {invitations.data.map((invite) => (
                  <tr key={invite.id} className="border-t border-line">
                    <td className="px-5 py-3 font-semibold text-ink">{invite.email}</td>
                    <td className="px-5 py-3">
                      <div className="flex flex-wrap gap-1">
                        {invite.role_codes.length === 0 ? <span className="text-xs text-faint">Default</span> : invite.role_codes.map((r) => <Tag key={r}>{r}</Tag>)}
                      </div>
                    </td>
                    <td className="px-5 py-3 text-ink-soft">{formatDate(invite.expires_at)}</td>
                    <td className="px-5 py-3 text-ink-soft">{formatDate(invite.created_at)}</td>
                    <td className="px-5 py-3 text-right">
                      <RowActions
                        label={invite.email}
                        actions={[{ label: "Revoke", tone: "danger", onSelect: () => setRevoking(invite), hidden: !canWrite }]}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ) : null}
      </section>

      {editing ? (
        <MemberForm
          member={editing}
          units={units.status === "success" ? units.data : []}
          locations={locations.status === "success" ? locations.data : []}
          onClose={() => setEditing(null)}
          onSaved={(m) => {
            toast.success(`${m.display_name ?? m.full_name} saved.`);
            members.reload();
          }}
        />
      ) : null}

      {assigningRoles ? (
        <RoleAssignDialog
          member={assigningRoles}
          roles={roles.status === "success" ? roles.data : []}
          onClose={() => setAssigningRoles(null)}
          onSaved={(m) => {
            toast.success(`Roles updated for ${m.display_name ?? m.full_name}.`);
            members.reload();
          }}
        />
      ) : null}

      {inviting ? (
        <InviteDialog
          roles={roles.status === "success" ? roles.data : []}
          onClose={() => setInviting(false)}
          onSaved={() => invitations.reload()}
        />
      ) : null}

      <ConfirmDialog
        open={removing !== null}
        onClose={() => {
          setRemoving(null);
          setRemoveReason("");
          remove.reset();
        }}
        onConfirm={() => removing && void remove.mutate(removing)}
        title={`Remove ${removing?.display_name ?? removing?.full_name ?? "this person"}?`}
        body="They lose access to every OpsGrids product immediately. Their account is not deleted, and re-inviting the same address restores this membership."
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      >
        <TextArea label="Reason" optional value={removeReason} onChange={(e) => setRemoveReason(e.target.value)} helper="Kept in this organisation's own records." />
      </ConfirmDialog>

      <ConfirmDialog
        open={revoking !== null}
        onClose={() => {
          setRevoking(null);
          revoke.reset();
        }}
        onConfirm={() => revoking && void revoke.mutate(revoking)}
        title={`Revoke the invitation to ${revoking?.email ?? "this address"}?`}
        body="The link stops working immediately. This can't be undone."
        confirmLabel="Revoke"
        pendingLabel="Revoking…"
        pending={revoke.pending}
        {...(revoke.error ? { error: problemMessage(revoke.error) } : {})}
      />
    </div>
  );
}

function MemberForm({
  member,
  units,
  locations,
  onClose,
  onSaved,
}: {
  member: Member;
  units: OrgUnit[];
  locations: Location[];
  onClose: () => void;
  onSaved: (member: Member) => void;
}) {
  const [displayName, setDisplayName] = useState(member.display_name ?? "");
  const [jobTitle, setJobTitle] = useState(member.job_title ?? "");
  const [orgUnitId, setOrgUnitId] = useState(member.org_unit_id ?? "");
  const [manager, setManager] = useState<ComboboxOption | null>(null);
  const [locationCodes, setLocationCodes] = useState<string[]>(member.location_codes);
  const [startedOn, setStartedOn] = useState(member.started_on ?? "");
  const nameRef = useRef<HTMLInputElement>(null);

  useAsync(async () => {
    if (!member.manager_membership_id) return null;
    const found = await getMember(member.manager_membership_id);
    setManager({ id: found.id, label: found.display_name ?? found.full_name, sublabel: found.email });
    return found;
  }, [member.manager_membership_id]);

  const save = useMutation((patch: MemberPatch) => updateMember(member.id, patch), {
    onSuccess: (updated) => {
      onClose();
      onSaved(updated);
    },
  });

  function submit() {
    const patch: MemberPatch = {};
    if (displayName.trim() !== (member.display_name ?? "")) patch.display_name = displayName.trim();
    if (jobTitle.trim() !== (member.job_title ?? "")) patch.job_title = jobTitle.trim();
    if (orgUnitId !== (member.org_unit_id ?? "")) patch.org_unit_id = orgUnitId || null;
    if ((manager?.id ?? null) !== member.manager_membership_id) patch.manager_membership_id = manager?.id ?? null;
    if (locationCodes.join(",") !== member.location_codes.join(",")) patch.location_codes = locationCodes;
    if (startedOn !== (member.started_on ?? "")) patch.started_on = startedOn || null;
    void save.mutate(patch);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Edit ${member.display_name ?? member.full_name}`}
      submitLabel="Save changes"
      pendingLabel="Saving…"
      pending={save.pending}
      size="lg"
      initialFocusRef={nameRef}
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? { error: problemMessage(save.error) }
        : {})}
    >
      <TextField label="Full name" value={member.full_name} disabled helper="Set from the person's account, not editable here." />
      <TextField label="Email" value={member.email} disabled helper="Set from the person's account, not editable here." />
      <TextField
        ref={nameRef}
        label="Display name"
        optional
        value={displayName}
        onChange={(e) => setDisplayName(e.target.value)}
        {...optionalProp("error", save.fieldErrors.display_name)}
      />
      <TextField
        label="Job title"
        optional
        value={jobTitle}
        onChange={(e) => setJobTitle(e.target.value)}
        {...optionalProp("error", save.fieldErrors.job_title)}
      />
      <Select
        label="Department"
        placeholder="No department"
        options={flattenOrgUnits(units).map(({ unit, depth }) => ({ value: unit.id, label: `${"— ".repeat(depth)}${unit.name}` }))}
        value={orgUnitId}
        onChange={(e) => setOrgUnitId(e.target.value)}
        {...optionalProp("error", save.fieldErrors.org_unit_id)}
      />
      <Combobox
        label="Manager"
        value={manager}
        onChange={setManager}
        loadOptions={async (q, signal) => {
          const page = await listMembers({ q, size: 10 });
          if (signal.aborted) return [];
          return page.items.filter((m) => m.id !== member.id).map((m) => ({ id: m.id, label: m.display_name ?? m.full_name, sublabel: m.email }));
        }}
        {...optionalProp("error", save.fieldErrors.manager_membership_id)}
      />
      <fieldset className="flex flex-col gap-1.5 border-0 p-0 m-0">
        <legend className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-faint">Locations</legend>
        <div className="flex flex-col gap-2">
          {locations.length === 0 ? (
            <p className="text-sm text-muted">No locations exist yet.</p>
          ) : (
            locations.map((loc) => (
              <label key={loc.code} className="flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={locationCodes.includes(loc.code)}
                  onChange={(e) =>
                    setLocationCodes((prev) => (e.target.checked ? [...prev, loc.code] : prev.filter((c) => c !== loc.code)))
                  }
                  className="size-4 accent-accent"
                />
                {loc.name}
              </label>
            ))
          )}
        </div>
      </fieldset>
      <TextField
        label="Started on"
        type="date"
        optional
        value={startedOn}
        onChange={(e) => setStartedOn(e.target.value)}
        {...optionalProp("error", save.fieldErrors.started_on)}
      />
    </FormDialog>
  );
}

function RoleAssignDialog({
  member,
  roles,
  onClose,
  onSaved,
}: {
  member: Member;
  roles: Role[];
  onClose: () => void;
  onSaved: (member: Member) => void;
}) {
  const [selected, setSelected] = useState<string[]>(member.roles);

  const save = useMutation((codes: string[]) => setMemberRoles(member.id, codes), {
    onSuccess: (updated) => {
      onClose();
      onSaved(updated);
    },
  });

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={() => void save.mutate(selected)}
      title={`Roles for ${member.display_name ?? member.full_name}`}
      description="Replaces this person's roles entirely — this is not additive."
      submitLabel="Save roles"
      pendingLabel="Saving…"
      pending={save.pending}
      {...(save.error ? { error: problemMessage(save.error) } : {})}
    >
      <div className="flex max-h-72 flex-col gap-2 overflow-y-auto">
        {roles.length === 0 ? (
          <p className="text-sm text-muted">No roles exist yet.</p>
        ) : (
          roles.map((role) => (
            <label key={role.code} className="flex items-start gap-2 rounded-tile border border-line p-3 text-sm">
              <input
                type="checkbox"
                checked={selected.includes(role.code)}
                onChange={(e) =>
                  setSelected((prev) => (e.target.checked ? [...prev, role.code] : prev.filter((c) => c !== role.code)))
                }
                className="mt-0.5 size-4 accent-accent"
              />
              <span className="flex flex-col">
                <span className="font-semibold text-ink">
                  {role.name} {role.is_system ? <Tag tone="neutral">System</Tag> : null}
                </span>
                {role.description ? <span className="text-xs text-muted">{role.description}</span> : null}
              </span>
            </label>
          ))
        )}
      </div>
    </FormDialog>
  );
}

function InviteDialog({ roles, onClose, onSaved }: { roles: Role[]; onClose: () => void; onSaved: () => void }) {
  const [email, setEmail] = useState("");
  const [roleCodes, setRoleCodes] = useState<string[]>([]);
  const [expiresInDays, setExpiresInDays] = useState("14");
  const [emailError, setEmailError] = useState<string | undefined>(undefined);
  const [created, setCreated] = useState<InvitationCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);

  const save = useMutation((payload: InvitationCreate) => createInvitation(payload), {
    onSuccess: (invitation) => {
      setCreated(invitation);
      onSaved();
    },
  });

  function submit() {
    setEmailError(undefined);
    if (!/.+@.+\..+/.test(email.trim())) {
      setEmailError("Enter a valid email address.");
      emailRef.current?.focus();
      return;
    }
    const days = Number(expiresInDays);
    void save.mutate({
      email: email.trim(),
      role_codes: roleCodes,
      product_codes: [],
      expires_in_days: Number.isFinite(days) && days >= 1 && days <= 90 ? days : 14,
    });
  }

  const link = created ? `${window.location.origin}/accept-invitation?token=${created.token}` : "";

  if (created) {
    return (
      <Dialog open onClose={onClose} title="Invitation created" description="This link is shown only once — copy it now. It will not be shown again.">
        <div className="flex flex-col gap-4">
          <InlineAlert tone="warning" title="This link is shown only once.">
            Send it to {created.email} yourself — the service does not email it.
          </InlineAlert>
          <div className="flex items-center gap-2 rounded-tile bg-ground px-4 py-3">
            <code className="min-w-0 flex-1 truncate text-sm text-ink">{link}</code>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                void navigator.clipboard.writeText(link).then(() => setCopied(true));
              }}
            >
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <div className="flex justify-end">
            <Button type="button" onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
      </Dialog>
    );
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Invite somebody"
      submitLabel="Create invitation"
      pendingLabel="Creating…"
      pending={save.pending}
      initialFocusRef={emailRef}
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? { error: problemMessage(save.error) }
        : {})}
    >
      <TextField
        ref={emailRef}
        label="Email"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        {...optionalProp("error", emailError ?? save.fieldErrors.email)}
      />
      <fieldset className="flex flex-col gap-1.5 border-0 p-0 m-0">
        <legend className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-faint">
          Roles (leave empty for the default role)
        </legend>
        <div className="flex max-h-48 flex-col gap-2 overflow-y-auto">
          {roles.map((role) => (
            <label key={role.code} className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={roleCodes.includes(role.code)}
                onChange={(e) =>
                  setRoleCodes((prev) => (e.target.checked ? [...prev, role.code] : prev.filter((c) => c !== role.code)))
                }
                className="size-4 accent-accent"
              />
              {role.name}
            </label>
          ))}
        </div>
      </fieldset>
      <TextField
        label="Expires in (days)"
        type="number"
        min={1}
        max={90}
        value={expiresInDays}
        onChange={(e) => setExpiresInDays(e.target.value)}
        {...optionalProp("error", save.fieldErrors.expires_in_days)}
      />
    </FormDialog>
  );
}
