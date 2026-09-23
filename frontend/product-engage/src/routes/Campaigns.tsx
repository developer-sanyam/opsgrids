import { useCallback, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Can, useAsync } from "@opsgrids/client";
import { MegaphoneIcon } from "@opsgrids/ui";

import { listCampaigns } from "@/api/client";
import type { CampaignOut, CampaignStatus } from "@/api/types";
import { formatInstant, formatNumber } from "@/lib/format";
import { audienceName, loadLists, resolveLists } from "@/lib/refs";
import type { ResolvedLists } from "@/lib/refs";
import { useRouteNotice } from "@/lib/useRouteNotice";
import {
  Announcer,
  Button,
  CampaignStatusTag,
  Card,
  EmptyState,
  LoadFailure,
  PageHeader,
  PageTitle,
  Pagination,
  Select,
  Skeleton,
} from "@/ui";
import type { SelectOption } from "@/ui";

/**
 * Every campaign this organization has drafted, sent or is sending, newest first. Stats are the
 * same grouped read `CampaignEditor` shows in full — `recipients`, `sent` and `failed` are the
 * three a person scanning a list of campaigns actually wants without opening one.
 */

const STATUS_OPTIONS: SelectOption[] = [
  { value: "", label: "All statuses" },
  { value: "draft", label: "Draft" },
  { value: "scheduled", label: "Scheduled" },
  { value: "sending", label: "Sending" },
  { value: "paused", label: "Paused" },
  { value: "sent", label: "Sent" },
  { value: "cancelled", label: "Cancelled" },
];

function CampaignRow({ campaign, resolved }: { campaign: CampaignOut; resolved: ResolvedLists }) {
  return (
    <tr className="border-t border-line align-top">
      <th scope="row" className="py-3 pr-4 text-left">
        <Link
          to={`/campaigns/${campaign.id}`}
          className="text-sm font-bold text-ink underline-offset-4 hover:text-accent-ink hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {campaign.name}
        </Link>
        <span className="mt-1 block text-xs text-muted">{audienceName(campaign, resolved)}</span>
      </th>
      <td className="py-3 pr-4">
        <CampaignStatusTag status={campaign.status} />
      </td>
      <td className="py-3 pr-4 text-sm tabular-nums text-ink">
        {formatNumber(campaign.stats.sent)} / {formatNumber(campaign.stats.recipients)}
        <span className="ml-1 text-xs font-normal text-muted">sent</span>
      </td>
      <td className="py-3 pr-4 text-sm tabular-nums text-negative">
        {campaign.stats.failed > 0 ? formatNumber(campaign.stats.failed) : "—"}
      </td>
      <td className="py-3 pr-4 text-sm text-muted">{formatInstant(campaign.updated_at)}</td>
    </tr>
  );
}

const NO_CAMPAIGNS: CampaignOut[] = [];
const NO_RESOLVED: ResolvedLists = { lists: [], gone: new Set() };

function Campaigns() {
  const navigate = useNavigate();
  const titleRef = useRef<HTMLSpanElement>(null);
  useRouteNotice(titleRef);
  const [status, setStatus] = useState<CampaignStatus | "">("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);

  /* The list names are decoration: a caller who may read campaigns but not lists still gets every
     row, with "A list" where the name would be. Lists the paged read did not reach are read by id,
     so a campaign addressed to the 2,301st list still names it. */
  const loader = useCallback(async () => {
    const [campaigns, lists] = await Promise.all([
      listCampaigns({ ...(status === "" ? {} : { status }), page, size }),
      loadLists(),
    ]);
    const ids = campaigns.items.flatMap((c) => (c.list_id === null ? [] : [c.list_id]));
    return { campaigns, resolved: await resolveLists(ids, lists.items) };
  }, [status, page, size]);
  const state = useAsync(loader, [status, page, size]);
  const campaigns = state.status === "success" ? state.data.campaigns.items : NO_CAMPAIGNS;
  const resolved = state.status === "success" ? state.data.resolved : NO_RESOLVED;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Email"
        title={<PageTitle ref={titleRef}>Campaigns</PageTitle>}
        description="Drafted, scheduled, sending, sent and cancelled — every campaign this organization has ever addressed to its audience."
        actions={
          <Can permission="engage.campaign.campaign.write" scope="ORG">
            <Button onClick={() => navigate("/campaigns/new")}>New campaign</Button>
          </Can>
        }
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-6">
        <Select
          label="Filter by status"
          containerClassName="sm:max-w-xs"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as CampaignStatus | "");
            setPage(1);
          }}
          options={STATUS_OPTIONS}
        />
      </Card>

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load your campaigns" />
      ) : null}

      {state.status === "success" && campaigns.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<MegaphoneIcon className="size-6" />}
            title={status === "" ? "No campaigns yet" : "No campaigns with that status"}
            description={
              status === ""
                ? "Draft a campaign once you have a usable sender and something to send it to."
                : "Clear the filter to see campaigns in every status."
            }
            {...(status === ""
              ? {
                  action: (
                    <Can permission="engage.campaign.campaign.write" scope="ORG">
                      <Button onClick={() => navigate("/campaigns/new")}>New campaign</Button>
                    </Can>
                  ),
                }
              : {})}
          />
        </Card>
      ) : null}

      {state.status === "success" && campaigns.length > 0 ? (
        <Card className="p-6">
          <div className="overflow-x-auto">
            <table className="w-full min-w-4xl border-collapse text-left">
              <caption className="sr-only">
                Campaigns, their status, delivery counts and when each last changed
              </caption>
              <thead>
                <tr className="text-xs font-bold uppercase tracking-wider text-faint">
                  <th scope="col" className="pb-2 pr-4">
                    Campaign
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Status
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Sent
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Failed
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Updated
                  </th>
                </tr>
              </thead>
              <tbody>
                {campaigns.map((campaign) => (
                  <CampaignRow key={campaign.id} campaign={campaign} resolved={resolved} />
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={state.data.campaigns.page}
            pages={state.data.campaigns.pages}
            total={state.data.campaigns.total}
            size={state.data.campaigns.size}
            noun="campaigns"
            onPageChange={setPage}
            onSizeChange={(n) => {
              setSize(n);
              setPage(1);
            }}
            className="mt-4"
          />
        </Card>
      ) : null}
    </div>
  );
}

/** No dialog on this screen announces anything yet, but the barrel stays consistent with every
 *  other list page in this product so a later action (bulk cancel, say) has somewhere to speak. */
export function CampaignsPage() {
  return (
    <Announcer>
      <Campaigns />
    </Announcer>
  );
}
