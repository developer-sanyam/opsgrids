import { useCallback, useState } from "react";

import { useAsync } from "@opsgrids/client";
import { ClipboardListIcon, UsersIcon } from "@opsgrids/ui";

import { listContactLists } from "@/api/client";
import type { ContactListOut } from "@/api/types";
import { formatInstant } from "@/lib/format";
import {
  Announcer,
  Button,
  Card,
  EmptyState,
  LoadFailure,
  PageHeader,
  Skeleton,
  useAnnounce,
} from "@/ui";
import { ContactListFormDialog } from "./ContactListFormDialog";
import { ListMembersDialog } from "./ListMembersDialog";

/**
 * Named contact lists — a campaign's audience narrowed to a group, rather than the whole book.
 * Membership is managed in its own dialog per list rather than inline here, because "who is on
 * this list" is its own read (`GET /engage/lists/{id}/members`) against the whole contact book,
 * not a property of the row this page already has.
 */

const NO_LISTS: ContactListOut[] = [];

function ListsScreen() {
  const announce = useAnnounce();
  const [attempt, setAttempt] = useState(0);
  const [creating, setCreating] = useState(false);
  const [managing, setManaging] = useState<ContactListOut | null>(null);

  const loader = useCallback(() => listContactLists(), []);
  const state = useAsync(loader, [attempt]);
  const lists = state.status === "success" ? state.data.items : NO_LISTS;

  function reload(message: string) {
    announce(message);
    setCreating(false);
    setManaging(null);
    setAttempt((current) => current + 1);
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Audience"
        title="Lists"
        description="Named groups of contacts a campaign can target."
        actions={<Button onClick={() => setCreating(true)}>Create a list</Button>}
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
            icon={<ClipboardListIcon className="size-6" />}
            title="No lists yet"
            description="A list is a named group of contacts — build one to target part of your audience."
            action={<Button onClick={() => setCreating(true)}>Create a list</Button>}
          />
        </Card>
      ) : null}

      {lists.length > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {lists.map((list) => (
            <Card key={list.id} className="flex flex-col gap-3 p-5">
              <div className="flex items-start justify-between gap-2">
                <h3 className="text-sm font-bold text-ink">{list.name}</h3>
                <UsersIcon className="size-4 shrink-0 text-faint" />
              </div>
              <p className="min-h-10 text-sm text-muted">
                {list.description === "" ? "No description." : list.description}
              </p>
              <p className="text-xs text-faint">Created {formatInstant(list.created_at)}</p>
              <Button variant="outline" size="sm" onClick={() => setManaging(list)}>
                Manage members
              </Button>
            </Card>
          ))}
        </div>
      ) : null}

      <ContactListFormDialog
        open={creating}
        onClose={() => setCreating(false)}
        onSaved={(_list, message) => reload(message)}
      />

      <ListMembersDialog
        list={managing}
        onClose={() => setManaging(null)}
        onChanged={(message) => reload(message)}
      />
    </div>
  );
}

export function ListsPage() {
  return (
    <Announcer>
      <ListsScreen />
    </Announcer>
  );
}
