import { useCallback, useState } from "react";

import { useAsync } from "@opsgrids/client";
import { OctagonIcon } from "@opsgrids/ui";

import { listContacts, listSuppressions } from "@/api/client";
import type { ContactOut, SuppressionOut } from "@/api/types";
import { formatInstant } from "@/lib/format";
import {
  Announcer,
  Button,
  Card,
  EmptyState,
  LoadFailure,
  PageHeader,
  Skeleton,
  SuppressionReasonTag,
  useAnnounce,
} from "@/ui";
import { SuppressionFormDialog } from "./SuppressionFormDialog";

/**
 * Who this organization may not send Engage campaigns to, and why.
 *
 * The email shown per row is resolved from the contact book rather than carried on
 * `SuppressionOut` (which holds only `contact_id`): the service keeps the suppression record
 * itself narrow and lets the one place that already knows the contact's address answer it,
 * rather than duplicating email onto every suppression row.
 */

const NO_SUPPRESSIONS: SuppressionOut[] = [];
const NO_CONTACTS: ContactOut[] = [];

function SuppressionsScreen() {
  const announce = useAnnounce();
  const [attempt, setAttempt] = useState(0);
  const [adding, setAdding] = useState(false);

  const loader = useCallback(() => Promise.all([listSuppressions(), listContacts()]), []);
  const state = useAsync(loader, [attempt]);

  const suppressions = state.status === "success" ? state.data[0].items : NO_SUPPRESSIONS;
  const contacts = state.status === "success" ? state.data[1].items : NO_CONTACTS;
  const contactById = new Map(contacts.map((c) => [c.id, c]));

  function reload(message: string) {
    announce(message);
    setAdding(false);
    setAttempt((current) => current + 1);
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Audience"
        title="Suppressions"
        description="Contacts no Engage campaign may send to — unsubscribed, bounced, complained, or added here directly."
        actions={
          state.status === "success" ? (
            <Button onClick={() => setAdding(true)}>Suppress a contact</Button>
          ) : null
        }
      />

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load your suppression list" />
      ) : null}

      {state.status === "success" && suppressions.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<OctagonIcon className="size-6" />}
            title="No suppressions yet"
            description="Nobody has unsubscribed, bounced, or been added here directly — every contact in the audience is currently sendable."
            action={<Button onClick={() => setAdding(true)}>Suppress a contact</Button>}
          />
        </Card>
      ) : null}

      {suppressions.length > 0 ? (
        <Card className="p-6">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] border-collapse text-left">
              <caption className="sr-only">
                Suppressed contacts, the reason, and when it was recorded
              </caption>
              <thead>
                <tr className="text-xs font-bold uppercase tracking-wider text-faint">
                  <th scope="col" className="pb-2 pr-4">
                    Contact
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Reason
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Note
                  </th>
                  <th scope="col" className="pb-2">
                    Recorded
                  </th>
                </tr>
              </thead>
              <tbody>
                {suppressions.map((s) => {
                  const contact = contactById.get(s.contact_id);
                  return (
                    <tr key={s.id} className="border-t border-line align-top">
                      <th scope="row" className="py-3 pr-4 text-left">
                        <span className="block font-mono text-sm break-all text-ink">
                          {contact?.email ?? s.contact_id}
                        </span>
                      </th>
                      <td className="py-3 pr-4">
                        <SuppressionReasonTag reason={s.reason} />
                      </td>
                      <td className="py-3 pr-4 text-sm text-muted">
                        {s.note === "" ? "—" : s.note}
                      </td>
                      <td className="py-3 text-sm text-muted">{formatInstant(s.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      <SuppressionFormDialog
        open={adding}
        onClose={() => setAdding(false)}
        onSaved={(_suppression, message) => reload(message)}
      />
    </div>
  );
}

export function SuppressionsPage() {
  return (
    <Announcer>
      <SuppressionsScreen />
    </Announcer>
  );
}
