import { useCallback, useEffect, useState } from "react";

import { UsersIcon } from "@opsgrids/ui";

import { listCampaignRecipients } from "@/api/client";
import type { CampaignRecipientOut, RecipientStatus } from "@/api/types";
import { formatInstant } from "@/lib/format";
import { usePolling } from "@/lib/usePolling";
import {
  Card,
  EmptyState,
  LoadFailure,
  Pagination,
  RecipientStatusTag,
  Select,
  Skeleton,
} from "@/ui";
import type { SelectOption } from "@/ui";

/**
 * Who a campaign was addressed to and what happened to each, addresses masked exactly as
 * `campaign_recipients.email_masked` holds them — there is no field on the wire this screen could
 * render a real address from.
 *
 * While the campaign is `sending` the page polls, so the table turns over as the driver works
 * through it (`usePolling` stops on a 401/403, backs off on anything else, and pauses in a hidden
 * tab). Once it has settled the table is read once and left alone.
 */

const STATUS_OPTIONS: SelectOption[] = [
  { value: "", label: "Every outcome" },
  { value: "queued", label: "Queued" },
  { value: "sent", label: "Sent" },
  { value: "failed", label: "Failed" },
  { value: "suppressed", label: "Suppressed" },
];

const LIVE_INTERVAL_MS = 5_000;

const NO_ROWS: CampaignRecipientOut[] = [];

function RecipientRow({ recipient }: { recipient: CampaignRecipientOut }) {
  return (
    <tr className="border-t border-line align-top">
      <th scope="row" className="py-3 pr-4 text-left font-mono text-sm text-ink">
        {recipient.email}
      </th>
      <td className="py-3 pr-4">
        <RecipientStatusTag status={recipient.status} />
      </td>
      <td className="py-3 pr-4 text-sm text-muted">
        {recipient.sent_at === null ? "—" : formatInstant(recipient.sent_at)}
      </td>
      <td className="py-3 text-sm text-muted">{recipient.error === "" ? "—" : recipient.error}</td>
    </tr>
  );
}

export function CampaignRecipients({ campaignId, live }: { campaignId: string; live: boolean }) {
  const [status, setStatus] = useState<RecipientStatus | "">("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);

  const loader = useCallback(
    () =>
      listCampaignRecipients(campaignId, {
        ...(status === "" ? {} : { status }),
        page,
        size,
      }),
    [campaignId, status, page, size],
  );
  const state = usePolling(loader, [campaignId, status, page, size], live ? LIVE_INTERVAL_MS : null);
  const rows = state.status === "success" ? state.data.items : NO_ROWS;

  useEffect(() => {
    if (state.status === "success" && rows.length === 0 && page > 1) setPage(page - 1);
  }, [state.status, rows.length, page]);

  return (
    <Card className="flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h2 className="text-base font-bold text-ink">Recipients</h2>
        <Select
          label="Filter by outcome"
          containerClassName="sm:w-56"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as RecipientStatus | "");
            setPage(1);
          }}
          options={STATUS_OPTIONS}
        />
      </div>

      {state.status === "success" && state.stopped ? (
        <p role="status" className="text-xs font-semibold text-ink">
          Live updates stopped — reload to refresh.
        </p>
      ) : null}

      {state.status === "loading" ? (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </div>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load this campaign's recipients" />
      ) : null}

      {state.status === "success" && rows.length === 0 ? (
        <EmptyState
          icon={<UsersIcon className="size-6" />}
          title={status === "" ? "No recipients yet" : "No recipients with that outcome"}
          description={
            status === ""
              ? live
                ? "Recipients appear here as the send works through the audience."
                : "Nobody was addressed: the audience had no contact that may be mailed."
              : "Clear the filter to see every recipient."
          }
        />
      ) : null}

      {state.status === "success" && rows.length > 0 ? (
        <>
          <div
            className="overflow-x-auto"
            role="region"
            aria-label="Recipients of this campaign"
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a horizontally scrolling region must be reachable by keyboard or its overflow is unreachable without a pointer (axe: scrollable-region-focusable); the aria-label names it when it takes focus.
            tabIndex={0}
          >
            <table className="w-full min-w-2xl border-collapse text-left">
              <caption className="sr-only">
                Recipients of this campaign, addresses masked, with the outcome of each
              </caption>
              <thead>
                <tr className="text-xs font-bold uppercase tracking-wider text-faint">
                  <th scope="col" className="pb-2 pr-4">
                    Recipient
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Outcome
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Sent
                  </th>
                  <th scope="col" className="pb-2">
                    Detail
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((recipient) => (
                  <RecipientRow key={recipient.id} recipient={recipient} />
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={state.data.page}
            pages={state.data.pages}
            total={state.data.total}
            size={state.data.size}
            noun="recipients"
            onPageChange={setPage}
            onSizeChange={(n) => {
              setSize(n);
              setPage(1);
            }}
          />
        </>
      ) : null}
    </Card>
  );
}
