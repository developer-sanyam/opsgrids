import { useState } from "react";

import { getOwners } from "@/api/client";
import type { Owner } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { Can, useAsync } from "@opsgrids/client";
import { UserPlusIcon, UsersIcon, useToast } from "@opsgrids/ui";
import { Avatar, Button, Card, DataTable, EmptyState, ErrorState, PageHeader, Skeleton } from "@/ui";
import type { Column } from "@/ui";
import { OwnerFormDialog } from "@/forms/OwnerFormDialog";

/**
 * The rep directory — `GET /customers/owners`. Every company / contact / deal create needs an
 * owner from here, which is why a production org's first write is always made on this screen.
 * There is no edit or deactivate: the backend doesn't offer it (owner rows are add-only today),
 * so this screen only ever lists and creates.
 */
export function TeamPage() {
  const toast = useToast();
  const directory = useDirectory();
  const [creating, setCreating] = useState(false);

  const owners = useAsync(getOwners, []);

  const columns: Column<Owner>[] = [
    {
      key: "name",
      header: "Name",
      render: (o) => (
        <div className="flex items-center gap-3">
          <Avatar name={o.full_name} size="sm" />
          <span className="font-semibold text-ink">{o.full_name}</span>
        </div>
      ),
    },
    { key: "email", header: "Email", render: (o) => <span className="text-ink-soft">{o.email}</span> },
    {
      key: "role",
      header: "Role",
      render: (o) => <span className="text-ink-soft">{o.role || "—"}</span>,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Workspace"
        title="Team Members"
        description="Everyone a company, contact or deal can be owned by."
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
            rows={owners.data}
            rowKey={(o) => o.id}
            emptyState={
              <EmptyState
                icon={<UsersIcon className="size-6" />}
                title="No team members yet"
                description="A production org starts with none — add the first rep so companies, contacts and deals have someone to belong to."
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
    </div>
  );
}
