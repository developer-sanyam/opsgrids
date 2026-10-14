import { useMemo, useState } from "react";

import { getOwners, updateOwner } from "@/api/client";
import type { Owner } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { UserPlusIcon, UsersIcon, useToast } from "@opsgrids/ui";
import {
  Avatar,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  RowActions,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { OwnerFormDialog } from "@/forms/OwnerFormDialog";
import { OwnerEditDialog } from "@/forms/OwnerEditDialog";

/**
 * The rep directory — `GET /customers/owners`. Every company / contact / deal / lead create
 * needs an owner from here, which is why a production org's first write is always made on this
 * screen. Edit and deactivate are `PATCH /customers/owners/{id}` — deactivating refuses (409)
 * while the rep still owns a live company or contact, surfaced here rather than silently
 * retried.
 */
export function TeamPage() {
  const toast = useToast();
  const directory = useDirectory();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Owner | null>(null);
  const [togglingActive, setTogglingActive] = useState<Owner | null>(null);

  const owners = useAsync(getOwners, []);
  const canWrite = useCan("crm.book.owner.write", "ORG");

  const toggleActive = useMutation(
    (owner: Owner) => updateOwner(owner.id, { is_active: !owner.is_active }),
    {
      onSuccess: (updated) => {
        setTogglingActive(null);
        toast.success(updated.is_active ? `${updated.full_name} reactivated.` : `${updated.full_name} deactivated.`);
        owners.reload();
        directory.reload();
      },
    },
  );

  const columns: Column<Owner>[] = [
    {
      key: "name",
      header: "Name",
      render: (o) => (
        <div className="flex items-center gap-3">
          <Avatar name={o.full_name} size="sm" />
          <span className={o.is_active ? "font-semibold text-ink" : "font-semibold text-faint"}>
            {o.full_name}
          </span>
          {!o.is_active ? (
            <span className="rounded-full bg-ground px-2 py-0.5 text-xs font-bold uppercase text-muted">
              Inactive
            </span>
          ) : null}
        </div>
      ),
    },
    { key: "email", header: "Email", render: (o) => <span className="text-ink-soft">{o.email}</span> },
    {
      key: "role",
      header: "Role",
      render: (o) => <span className="text-ink-soft">{o.role || "—"}</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (o) => (
        <RowActions
          label={o.full_name}
          actions={[
            { label: "Edit", onSelect: () => setEditing(o), hidden: !canWrite },
            {
              label: o.is_active ? "Deactivate" : "Reactivate",
              ...(o.is_active ? { tone: "danger" as const } : {}),
              onSelect: () => setTogglingActive(o),
              hidden: !canWrite,
            },
          ]}
        />
      ),
    },
  ];

  const rows = useMemo(() => (owners.status === "success" ? owners.data : []), [owners]);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Workspace"
        title="Team Members"
        description="Everyone a company, contact, deal or lead can be owned by."
        actions={
          <Can permission="crm.book.owner.write" scope="ORG">
            <Button leadingIcon={<UserPlusIcon className="size-4" />} onClick={() => setCreating(true)}>
              Add team member
            </Button>
          </Can>
        }
      />

      <Card className="p-2 sm:p-4">
        {owners.status === "loading" ? (
          <div className="flex flex-col gap-3 p-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {owners.status === "error" ? (
          <ErrorState
            title="Couldn't load the team"
            message={owners.error.message}
            onRetry={owners.reload}
          />
        ) : null}
        {owners.status === "success" ? (
          <DataTable
            caption="Team members"
            columns={columns}
            rows={rows}
            rowKey={(o) => o.id}
            emptyState={
              <EmptyState
                icon={<UsersIcon className="size-6" />}
                title="No team members yet"
                description="A production org starts with none — add the first rep so companies, contacts, deals and leads have someone to belong to."
                action={
                  <Can permission="crm.book.owner.write" scope="ORG">
                    <Button onClick={() => setCreating(true)}>Add team member</Button>
                  </Can>
                }
              />
            }
          />
        ) : null}
      </Card>

      {creating ? (
        <OwnerFormDialog
          onClose={() => setCreating(false)}
          onSaved={(owner) => {
            setCreating(false);
            toast.success(`${owner.full_name} added.`);
            owners.reload();
            directory.reload();
          }}
        />
      ) : null}

      {editing ? (
        <OwnerEditDialog
          owner={editing}
          onClose={() => setEditing(null)}
          onSaved={(owner) => {
            setEditing(null);
            toast.success(`${owner.full_name} saved.`);
            owners.reload();
            directory.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={togglingActive !== null}
        onClose={() => {
          setTogglingActive(null);
          toggleActive.reset();
        }}
        onConfirm={() => togglingActive && void toggleActive.mutate(togglingActive)}
        title={togglingActive?.is_active ? "Deactivate this rep?" : "Reactivate this rep?"}
        body={
          togglingActive?.is_active
            ? `${togglingActive.full_name} can no longer be assigned new records. This refuses if they still own a live company or contact — reassign those first.`
            : `${togglingActive?.full_name} can be assigned records again.`
        }
        confirmLabel={togglingActive?.is_active ? "Deactivate" : "Reactivate"}
        pendingLabel="Saving…"
        pending={toggleActive.pending}
        {...(toggleActive.error ? { error: problemMessage(toggleActive.error) } : {})}
      />
    </div>
  );
}
