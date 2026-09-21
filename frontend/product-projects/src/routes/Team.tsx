import { useCallback, useState } from "react";

import { DEFAULT_PAGE_SIZE, deleteMember, listMembers } from "@/api/client";
import type { MemberOut } from "@/api/types";
import { Can, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import {
  Avatar,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  RowActions,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { UsersIcon, useToast } from "@opsgrids/ui";
import { MemberFormDialog } from "./MemberFormDialog";

const WRITE = "projects.team.member.write";

export function TeamPage() {
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(DEFAULT_PAGE_SIZE);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<MemberOut | null>(null);
  const [deleting, setDeleting] = useState<MemberOut | null>(null);

  const members = useAsync(
    useCallback(() => listMembers({ q: query, page, size }), [query, page, size]),
    [query, page, size],
  );

  const remove = useMutation((member: MemberOut) => deleteMember(member.id), {
    onSuccess: (_result, member) => {
      setDeleting(null);
      toast.success(`${member.name} removed from the team.`);
      members.reload();
    },
  });

  if (members.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader eyebrow="Workspace" title="Team" />
        <Card className="p-4 sm:p-6 flex flex-col gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full rounded-tile" />
          ))}
        </Card>
      </div>
    );
  }

  if (members.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader eyebrow="Workspace" title="Team" />
        <Card className="p-6">
          <ErrorState
            title="Couldn't load the team"
            message={problemMessage(members.error)}
            action={<Button onClick={members.reload}>Try again</Button>}
          />
        </Card>
      </div>
    );
  }

  const { items, total, pages } = members.data;
  const firstRun = total === 0 && query === "";

  const columns: Column<MemberOut>[] = [
    {
      key: "name",
      header: "Name",
      render: (m) => (
        <span className="inline-flex items-center gap-2.5">
          <Avatar name={m.name} size="sm" />
          <span className="flex flex-col leading-tight">
            <span className="font-semibold text-ink">{m.name}</span>
            <span className="text-xs text-faint tnum">{m.id}</span>
          </span>
        </span>
      ),
    },
    {
      key: "role",
      header: "Role",
      render: (m) => <span className="text-ink-soft">{m.role || "—"}</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (m) => (
        <Can permission={WRITE}>
          <RowActions
            label={m.name}
            actions={[
              { label: "Edit", onSelect: () => setEditing(m) },
              { label: "Remove", tone: "danger", onSelect: () => setDeleting(m) },
            ]}
          />
        </Can>
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Workspace"
        title="Team"
        description="Every owner, assignee and delivery lead across the portfolio is named here — the roster every picker in this product reads from."
        actions={
          <Can permission={WRITE}>
            <Button onClick={() => setCreating(true)}>Add member</Button>
          </Can>
        }
      />

      <Card className="p-2 sm:p-4 flex flex-col gap-4">
        <div className="px-2 pt-2 sm:px-0 sm:pt-0">
          <SearchInput
            label="Search team members"
            placeholder="Search by name or reference…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(1);
            }}
            containerClassName="max-w-sm"
          />
        </div>

        <DataTable
          caption="Delivery-org team roster"
          columns={columns}
          rows={items}
          rowKey={(m) => m.id}
          emptyState={
            firstRun ? (
              <EmptyState
                icon={<UsersIcon className="size-6" />}
                title="No one on the team yet"
                description="Add the first member so projects, milestones and cards have an owner or assignee to pick from."
                action={
                  <Can permission={WRITE}>
                    <Button onClick={() => setCreating(true)}>Add member</Button>
                  </Can>
                }
              />
            ) : (
              <EmptyState
                icon={<UsersIcon className="size-6" />}
                title="No members match that search"
                description="Try a shorter search term."
                action={
                  <Button variant="outline" onClick={() => setQuery("")}>
                    Clear search
                  </Button>
                }
              />
            )
          }
        />
        {items.length > 0 ? (
          <Pagination
            page={page}
            pages={pages}
            total={total}
            size={size}
            noun="members"
            onPageChange={setPage}
            onSizeChange={(n) => {
              setSize(n);
              setPage(1);
            }}
          />
        ) : null}
      </Card>

      {creating ? (
        <MemberFormDialog
          member={null}
          onClose={() => setCreating(false)}
          onSaved={(_member, message) => {
            toast.success(message);
            members.reload();
          }}
        />
      ) : null}
      {editing ? (
        <MemberFormDialog
          member={editing}
          onClose={() => setEditing(null)}
          onSaved={(_member, message) => {
            setEditing(null);
            toast.success(message);
            members.reload();
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
        title={deleting ? `Remove ${deleting.name}?` : "Remove this member?"}
        body="Every project or card they own or are assigned to reads as Unassigned from then on. This can't be undone."
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
