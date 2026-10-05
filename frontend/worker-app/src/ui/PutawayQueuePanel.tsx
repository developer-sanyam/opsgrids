import { useState } from "react";

import { Button, Card, ConfirmDialog, InlineAlert } from "@opsgrids/ui";

import { formatQty } from "@/lib/quantity";
import type { PutawayQueueEntry } from "@/lib/putawayQueue";
import { usePutawayQueue } from "@/lib/putawayQueueContext";
import { WifiOffIcon } from "@/ui/icons";

function describe(entry: PutawayQueueEntry): string {
  const { summary, body } = entry;
  return `${formatQty(body.quantity)} ${summary.uom} of ${summary.sku} to ${summary.toBarcode}`;
}

/**
 * Everything the put-away queue wants a worker to know, in one place: offline, saved-but-not-yet-
 * synced put-aways, and the ones the server refused for good — each with the server's own reason
 * and a way to discard it. Renders nothing when there is nothing to say.
 */
export function PutawayQueuePanel() {
  const queue = usePutawayQueue();
  const [discarding, setDiscarding] = useState<PutawayQueueEntry | null>(null);
  const waiting = queue.entries.filter((entry) => entry.status === "pending");

  return (
    <>
      {!queue.online ? (
        <InlineAlert tone="warning" title="You're offline">
          <span className="inline-flex items-center gap-2">
            <WifiOffIcon className="size-4" aria-hidden />
            Put-aways are saved on this phone and sync by themselves once you're back online with the app open.
          </span>
        </InlineAlert>
      ) : null}

      {!queue.persistent ? (
        <InlineAlert tone="warning" title="This phone can't save put-aways while offline">
          If the signal drops, keep this app open until they sync — closing it would lose them.
        </InlineAlert>
      ) : null}

      {waiting.length > 0 ? (
        <Card className="flex flex-col gap-3 p-4">
          <InlineAlert
            tone={queue.syncing ? "info" : "warning"}
            title={
              queue.syncing
                ? "Syncing put-aways…"
                : `${waiting.length} put-away${waiting.length === 1 ? "" : "s"} waiting to sync`
            }
            action={
              queue.online ? (
                <Button variant="secondary" size="sm" onClick={queue.syncNow} disabled={queue.syncing}>
                  Sync now
                </Button>
              ) : undefined
            }
          >
            {queue.persistent
              ? "Saved on this phone. They sync whenever the app is open and you're online."
              : "Held in this app only — keep it open until they sync."}
          </InlineAlert>
          <ul className="flex flex-col gap-2">
            {waiting.map((entry) => (
              <li key={entry.idempotencyKey} className="rounded-tile bg-ground p-3 text-sm text-ink">
                {describe(entry)}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {queue.attention.length > 0 ? (
        <Card className="flex flex-col gap-3 p-4">
          <h2 className="text-sm font-semibold text-muted">Needs attention</h2>
          <ul className="flex flex-col gap-3">
            {queue.attention.map((entry) => (
              <li key={entry.idempotencyKey}>
                <InlineAlert
                  tone="danger"
                  title={`Not recorded: ${describe(entry)}`}
                  action={
                    <Button variant="secondary" size="sm" onClick={() => setDiscarding(entry)}>
                      Discard
                    </Button>
                  }
                >
                  {entry.attention?.message ?? "The server refused this put-away."}
                </InlineAlert>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      <ConfirmDialog
        open={discarding !== null}
        onClose={() => setDiscarding(null)}
        onConfirm={() => {
          if (discarding) queue.discard(discarding.idempotencyKey);
          setDiscarding(null);
        }}
        title="Discard this put-away?"
        body={
          discarding
            ? `${describe(discarding)} was refused and won't be recorded. Discarding removes it from this phone; the stock stays where the system has it.`
            : ""
        }
        confirmLabel="Discard"
      />
    </>
  );
}
