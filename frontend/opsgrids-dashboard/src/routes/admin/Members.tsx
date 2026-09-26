import { useRef, useState } from "react";

import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  CheckboxGroup,
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
  StatusBadge,
  Tag,
  TextArea,
  TextField,
  statusTone,
  useDebouncedValue,
  useToast,
} from "@opsgrids/ui";
import type { Column, ComboboxOption, TableSelection } from "@opsgrids/ui";

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
import { downloadCsv } from "@/lib/csv";
import { formatDate } from "@/lib/money";
import { flattenOrgUnits } from "@/lib/orgUnits";
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
  const query = useDebouncedValue(rawQuery);
  const [orgUnitId, setOrgUnitId] = useState("");
  const [status, setStatus] = useState("");
  const [linked, setLinked] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

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
      mobile: "title",
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
    {
      key: "status",
      header: "Status",
      mobile: "status",
      render: (m) => <StatusBadge tone={statusTone(m.status) ?? "neutral"} label={m.status} />,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
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

  const memberSelection: TableSelection<Member> | undefined =
    members.status === "success"
      ? {
          selected,
          onToggle: (key, next) =>
            setSelected((prev) => {
              const nextSet = new Set(prev);
              if (next) nextSet.add(key);
              else nextSet.delete(key);
              return nextSet;
            }),
          onToggleAll: (next) =>
            setSelected(next ? new Set(members.data.items.map((m) => m.id)) : new Set()),
          label: (m) => m.display_name ?? m.full_name,
        }
      : undefined;

  function exportSelectedMembers() {
    if (members.status !== "success") return;
    const chosen = members.data.items.filter((m) => selected.has(m.id));
    downloadCsv(
      "members.csv",
      ["Name", "Email", "Job title", "Department", "Status"],
      chosen.map((m) => [
        m.display_name ?? m.full_name,
        m.email,
        m.job_title ?? "",
        units.status === "success" ? departmentName(units.data, m.org_unit_id) : "",
        m.status,
      ]),
    );
  }

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
              mobileCard="auto"
              selection={memberSelection}
              bulkActions={
                <Button variant="outline" size="sm" onClick={exportSelectedMembers}>
                  Export selected
                </Button>
              }
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
        {invitations.status === "success" ? (
          <Card className="p-0">
            <DataTable
              rows={invitations.data}
              rowKey={(i) => i.id}
              mobileCard="auto"
              columns={[
                { key: "email", header: "Email", mobile: "title", render: (i) => <span className="font-semibold text-ink">{i.email}</span> },
                {
                  key: "roles",
                  header: "Roles",
                  render: (i) => (
                    <div className="flex flex-wrap gap-1">
                      {i.role_codes.length === 0 ? <span className="text-xs text-faint">Default</span> : i.role_codes.map((r) => <Tag key={r}>{r}</Tag>)}
                    </div>
                  ),
                },
                { key: "expires", header: "Expires", render: (i) => <span className="text-ink-soft">{formatDate(i.expires_at)}</span> },
                { key: "created", header: "Created", render: (i) => <span className="text-ink-soft">{formatDate(i.created_at)}</span> },
                {
                  key: "actions",
                  header: "",
                  align: "right",
                  mobile: "actions",
                  render: (i) => (
                    <RowActions
                      label={i.email}
                      actions={[{ label: "Revoke", tone: "danger", onSelect: () => setRevoking(i), hidden: !canWrite }]}
                    />
                  ),
                },
              ]}
              emptyState={
                <EmptyState
                  title="No invitations are waiting to be accepted"
                  description="Invite someone above and their invitation appears here until they accept it."
                />
              }
            />
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

  // The same comparison `submit` below uses to build the patch — nothing to send means nothing
  // changed. `manager` starts `null` and is filled in from `member.manager_membership_id` by the
  // fetch below, so once it lands the two agree and this stays false until someone picks a new one.
  const dirty =
    displayName.trim() !== (member.display_name ?? "") ||
    jobTitle.trim() !== (member.job_title ?? "") ||
    orgUnitId !== (member.org_unit_id ?? "") ||
    (manager?.id ?? null) !== member.manager_membership_id ||
    locationCodes.join(",") !== member.location_codes.join(",") ||
    startedOn !== (member.started_on ?? "");

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
      dirty={dirty}
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
        error={save.fieldErrors.display_name}
      />
      <TextField
        label="Job title"
        optional
        value={jobTitle}
        onChange={(e) => setJobTitle(e.target.value)}
        error={save.fieldErrors.job_title}
      />
      <Select
        label="Department"
        placeholder="No department"
        options={flattenOrgUnits(units).map(({ unit, depth }) => ({ value: unit.id, label: `${"— ".repeat(depth)}${unit.name}` }))}
        value={orgUnitId}
        onChange={(e) => setOrgUnitId(e.target.value)}
        error={save.fieldErrors.org_unit_id}
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
        error={save.fieldErrors.manager_membership_id}
      />
      {locations.length === 0 ? (
        <p className="text-sm text-muted">No locations exist yet.</p>
      ) : (
        <CheckboxGroup
          label="Locations"
          options={locations.map((loc) => ({ value: loc.code, label: loc.name }))}
          value={locationCodes}
          onChange={setLocationCodes}
        />
      )}
      <TextField
        label="Started on"
        type="date"
        optional
        value={startedOn}
        onChange={(e) => setStartedOn(e.target.value)}
        error={save.fieldErrors.started_on}
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

  // Order-independent: a checkbox group's own order isn't meaningful, only the set is.
  const dirty =
    JSON.stringify([...selected].sort()) !== JSON.stringify([...member.roles].sort());

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
      dirty={dirty}
      {...(save.error ? { error: problemMessage(save.error) } : {})}
    >
      {roles.length === 0 ? (
        <p className="text-sm text-muted">No roles exist yet.</p>
      ) : (
        <div className="max-h-72 overflow-y-auto">
          <CheckboxGroup
            label="Roles"
            options={roles.map((role) => ({
              value: role.code,
              label: role.is_system ? `${role.name} (System)` : role.name,
              description: role.description ?? undefined,
            }))}
            value={selected}
            onChange={setSelected}
          />
        </div>
      )}
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

  // Create-only dialog: dirty against the blank state it opened on.
  const dirty = email !== "" || roleCodes.length > 0 || expiresInDays !== "14";

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
      dirty={dirty}
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
        required
        error={emailError ?? save.fieldErrors.email}
      />
      <div className="max-h-48 overflow-y-auto">
        <CheckboxGroup
          label="Roles"
          helper="Leave empty for the default role."
          options={roles.map((role) => ({ value: role.code, label: role.name }))}
          value={roleCodes}
          onChange={setRoleCodes}
        />
      </div>
      <TextField
        label="Expires in (days)"
        type="number"
        min={1}
        max={90}
        value={expiresInDays}
        onChange={(e) => setExpiresInDays(e.target.value)}
        error={save.fieldErrors.expires_in_days}
      />
    </FormDialog>
  );
}
