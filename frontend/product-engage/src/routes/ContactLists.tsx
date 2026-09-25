import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { Can, useAsync, useCan } from "@opsgrids/client";
import { LayersIcon } from "@opsgrids/ui";

import { deleteList, listLists } from "@/api/client";
import type { ContactListOut } from "@/api/types";
import { downloadCsv } from "@/lib/csv";
import { failureMessage } from "@/lib/errors";
import { useFocusHeading } from "@/lib/useFocusHeading";
import { formatInstant, formatNumber } from "@/lib/format";
import {
  Announcer,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  LoadFailure,
  PageHeading,
  Pagination,
  RowActions,
  Skeleton,
  useAnnounce,
} from "@/ui";
import type { Column, TableSelection } from "@/ui";
import { ContactListFormDialog } from "./ContactListFormDialog";

/**
 * Named slices of the audience a campaign can be addressed to. `member_count` is folded by the
 * service from `contact_list_members` on every read — this screen never counts a page of members
 * itself, which would only ever be the count of the one page it happened to fetch.
 */

const NO_LISTS: ContactListOut[] = [];

function ContactLists() {
  const announce = useAnnounce();
  const canWrite = useCan("engage.audience.list.write", "ORG");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<ContactListOut | null>(null);
  const [deleting, setDeleting] = useState<ContactListOut | null>(null);
  const [deleteFailure, setDeleteFailure] = useState<string | null>(null);
  const [deletePending, setDeletePending] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const loader = useCallback(() => listLists({ page, size }), [page, size]);
  const state = useAsync(loader, [page, size]);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const focusHeading = useFocusHeading(titleRef);
  const lists = state.status === "success" ? state.data.items : NO_LISTS;

  // Deleting the last row on a page beyond the first empties that page without emptying the
  // list — see `Contacts.tsx` for the same rule and the same reason.
  useEffect(() => {
    if (state.status === "success" && lists.length === 0 && page > 1) {
      setPage(page - 1);
    }
  }, [state.status, lists.length, page]);

  function reload(message: string) {
    announce(message);
    setCreating(false);
    setRenaming(null);
    // The row or button that was just used is about to be unmounted by the re-read.
    focusHeading();
    state.reload();
  }

  async function confirmDelete() {
    if (deleting === null || deletePending) return;
    setDeletePending(true);
    setDeleteFailure(null);
    try {
      await deleteList(deleting.id);
      const name = deleting.name;
      setDeleting(null);
      reload(`${name} deleted. Its contacts stay in your audience.`);
    } catch (caught) {
      setDeleteFailure(failureMessage(caught));
    } finally {
      setDeletePending(false);
    }
  }

  const columns: Column<ContactListOut>[] = [
    {
      key: "list",
      header: "List",
      mobile: "title",
      render: (list) => (
        <div className="flex flex-col">
          <Link
            to={`/lists/${list.id}`}
            className="text-sm font-bold text-ink underline-offset-4 hover:text-accent-ink hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            {list.name}
          </Link>
          {list.description !== "" ? <span className="text-xs text-muted">{list.description}</span> : null}
        </div>
      ),
    },
    { key: "members", header: "Members", cellClassName: "tnum", render: (list) => formatNumber(list.member_count) },
    { key: "updated", header: "Updated", render: (list) => <span className="text-muted">{formatInstant(list.updated_at)}</span> },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (list) => (
        <RowActions
          label={list.name}
          actions={[
            { label: "Rename", onSelect: () => setRenaming(list), hidden: !canWrite },
            { label: "Delete", tone: "danger", onSelect: () => setDeleting(list), hidden: !canWrite },
          ]}
        />
      ),
    },
  ];

  const listSelection: TableSelection<ContactListOut> | undefined =
    state.status === "success"
      ? {
          selected,
          onToggle: (key, next) =>
            setSelected((prev) => {
              const nextSet = new Set(prev);
              if (next) nextSet.add(key);
              else nextSet.delete(key);
              return nextSet;
            }),
          onToggleAll: (next) => setSelected(next ? new Set(lists.map((l) => l.id)) : new Set()),
          label: (list) => list.name,
        }
      : undefined;

  function exportSelectedLists() {
    const chosen = lists.filter((list) => selected.has(list.id));
    downloadCsv(
      "contact-lists.csv",
      ["List", "Description", "Members", "Updated"],
      chosen.map((list) => [list.name, list.description, String(list.member_count), formatInstant(list.updated_at)]),
    );
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeading
        ref={titleRef}
        eyebrow="Audience"
        title="Lists"
        description="Named slices of your audience a campaign can be addressed to. Deleting a list never deletes the contacts on it."
        actions={
          <Can permission="engage.audience.list.write" scope="ORG">
            <Button onClick={() => setCreating(true)}>Create a list</Button>
          </Can>
        }
      />

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load your lists" />
      ) : null}

      {state.status === "success" && lists.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<LayersIcon className="size-6" />}
            title="No lists yet"
            description="Create a list to address a campaign at a named slice of your audience, rather than everyone you may mail."
            action={
              <Can permission="engage.audience.list.write" scope="ORG">
                <Button onClick={() => setCreating(true)}>Create a list</Button>
              </Can>
            }
          />
        </Card>
      ) : null}

      {state.status === "success" && lists.length > 0 ? (
        <Card className="p-6">
          <DataTable
            caption="Contact lists, their size and when each last changed"
            rows={lists}
            rowKey={(list) => list.id}
            columns={columns}
            mobileCard="auto"
            selection={listSelection}
            bulkActions={
              <Button variant="outline" size="sm" onClick={exportSelectedLists}>
                Export selected
              </Button>
            }
          />
          <Pagination
            page={state.data.page}
            pages={state.data.pages}
            total={state.data.total}
            size={state.data.size}
            noun="lists"
            onPageChange={setPage}
            onSizeChange={(n) => {
              setSize(n);
              setPage(1);
            }}
            className="mt-4"
          />
        </Card>
      ) : null}

      <ContactListFormDialog
        open={creating}
        list={null}
        onClose={() => setCreating(false)}
        onSaved={(_list, message) => reload(message)}
      />

      <ContactListFormDialog
        open={renaming !== null}
        list={renaming}
        onClose={() => setRenaming(null)}
        onSaved={(_list, message) => reload(message)}
      />

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          setDeleteFailure(null);
        }}
        onConfirm={() => void confirmDelete()}
        title="Delete this list?"
        body={`${deleting?.name} is removed. Its ${deleting?.member_count === 1 ? "1 contact stays" : `${formatNumber(deleting?.member_count ?? 0)} contacts stay`} in your audience — only the list and its memberships go. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={deletePending}
        {...(deleteFailure !== null ? { error: deleteFailure } : {})}
      />
    </div>
  );
}

/** The screen's single polite live region — every dialog announces its result into it. */
export function ContactListsPage() {
  return (
    <Announcer>
      <ContactLists />
    </Announcer>
  );
}
