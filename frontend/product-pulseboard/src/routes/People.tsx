import { useCallback, useState } from "react";

import { DEFAULT_PAGE_SIZE, deletePerson, listPeople } from "@/api/client";
import type { Person } from "@/api/types";
import { Can, useAsync, useMutation, problemMessage } from "@opsgrids/client";
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
  Skeleton,
  UsersIcon,
  useToast,
} from "@opsgrids/ui";
import type { Column } from "@opsgrids/ui";
import { PersonFormDialog } from "./PersonFormDialog";

export function PeoplePage() {
  const toast = useToast();
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(DEFAULT_PAGE_SIZE);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Person | null>(null);
  const [deleting, setDeleting] = useState<Person | null>(null);

  const people = useAsync(useCallback(() => listPeople({ page, size }), [page, size]), [page, size]);

  const remove = useMutation((person: Person) => deletePerson(person.id), {
    onSuccess: (_result, person) => {
      setDeleting(null);
      toast.success(`${person.name} removed from the roster.`);
      people.reload();
    },
  });

  if (people.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader eyebrow="Delivery" title="People" />
        <Card className="p-4 sm:p-6 flex flex-col gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full rounded-tile" />
          ))}
        </Card>
      </div>
    );
  }

  if (people.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader eyebrow="Delivery" title="People" />
        <Card className="p-6">
          <ErrorState
            title="Couldn't load the roster"
            message={problemMessage(people.error)}
            action={<Button onClick={people.reload}>Try again</Button>}
          />
        </Card>
      </div>
    );
  }

  const { items, total, pages } = people.data;

  const columns: Column<Person>[] = [
    {
      key: "name",
      header: "Name",
      render: (p) => (
        <span className="inline-flex items-center gap-2.5">
          <Avatar name={p.name} size="sm" />
          <span className="flex flex-col leading-tight">
            <span className="font-semibold text-ink">{p.name}</span>
            <span className="text-xs text-faint tnum">{p.id}</span>
          </span>
        </span>
      ),
    },
    {
      key: "role",
      header: "Role",
      render: (p) => <span className="text-ink-soft">{p.role || "—"}</span>,
    },
    {
      key: "location",
      header: "Location",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (p) => <span className="text-ink-soft">{p.location || "—"}</span>,
    },
    {
      key: "capacity",
      header: "Capacity",
      align: "right",
      render: (p) => <span className="tnum font-semibold text-ink">{p.capacity_hours}h/wk</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (p) => (
        <Can permission="pulseboard.team.person.write">
          <RowActions
            label={p.name}
            actions={[
              { label: "Edit", onSelect: () => setEditing(p) },
              { label: "Remove", tone: "danger", onSelect: () => setDeleting(p) },
            ]}
          />
        </Can>
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Delivery"
        title="People"
        description="The delivery team roster. Every project owner, task assignee and timesheet entry names someone here."
        actions={
          <Can permission="pulseboard.team.person.write">
            <Button onClick={() => setCreating(true)}>Add a person</Button>
          </Can>
        }
      />

      <Card className="p-2 sm:p-4">
        <DataTable
          caption="The delivery-team roster"
          columns={columns}
          rows={items}
          rowKey={(p) => p.id}
          emptyState={
            <EmptyState
              icon={<UsersIcon className="size-6" />}
              title="No one on the roster yet"
              description="Add the first person so projects and tasks have someone to assign to."
              action={
                <Can permission="pulseboard.team.person.write">
                  <Button onClick={() => setCreating(true)}>Add a person</Button>
                </Can>
              }
            />
          }
        />
        {items.length > 0 ? (
          <Pagination
            page={page}
            pages={pages}
            total={total}
            size={size}
            noun="people"
            onPageChange={setPage}
            onSizeChange={(n) => {
              setSize(n);
              setPage(1);
            }}
          />
        ) : null}
      </Card>

      {creating ? (
        <PersonFormDialog
          person={null}
          onClose={() => setCreating(false)}
          onSaved={(_person, message) => {
            toast.success(message);
            people.reload();
          }}
        />
      ) : null}
      {editing ? (
        <PersonFormDialog
          person={editing}
          onClose={() => setEditing(null)}
          onSaved={(_person, message) => {
            setEditing(null);
            toast.success(message);
            people.reload();
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
        title={deleting ? `Remove ${deleting.name}?` : "Remove this person?"}
        body="They're taken off any project or task they own or are assigned to, and their name reads as Unassigned from then on. This can't be undone."
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
