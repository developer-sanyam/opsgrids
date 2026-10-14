import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Can, useAsync } from "@opsgrids/client";
import { MegaphoneIcon, Tabs } from "@opsgrids/ui";
import type { TabItem } from "@opsgrids/ui";

import { countCampaignsByStatus } from "@/api/email";
import type { CampaignCounts } from "@/api/email";
import { FULL_PAGE, listCampaigns } from "@/api/client";
import type { CampaignOut, CampaignStatus } from "@/api/types";
import { formatInstant, formatNumber } from "@/lib/format";
import {
  Button,
  CampaignStatusTag,
  Card,
  EmptyState,
  LoadFailure,
  PageHeading,
  SearchInput,
  Skeleton,
} from "@/ui";

/**
 * Campaigns by status, with the count in each tab, a search, and a row per campaign.
 *
 * The design's Opened and Clicked columns are not here: the campaign list carries recipient and
 * delivery counts only, and open and click rates live on each campaign's analytics route. A column
 * of dashes for every row would say "nothing was opened" about a number nobody measured. The
 * design's Type column (marketing or automated) has no field behind it either.
 *
 * Counts come from the service's own `total` for each status, so a tab's number is the number of
 * rows it opens at any organization size. The list shows the newest page of the selected status;
 * search runs over what was loaded, and the footer says when there are more than that.
 */

type Tab = CampaignStatus | "all";

const TAB_LABELS: { id: Tab; label: string }[] = [
  { id: "all", label: "All" },
  { id: "draft", label: "Draft" },
  { id: "scheduled", label: "Scheduled" },
  { id: "sending", label: "Sending" },
  { id: "paused", label: "Paused" },
  { id: "sent", label: "Sent" },
  { id: "cancelled", label: "Cancelled" },
];

function tabItems(counts: CampaignCounts | null): TabItem[] {
  return TAB_LABELS.map(({ id, label }) => ({
    id,
    label,
    ...(counts === null ? {} : { count: counts[id] }),
  }));
}

/** When the campaign went, or will go. A scheduled row says so in words: the badge already names
 *  the status, and the instant alone would read as though the campaign had gone out. */
function whenText(campaign: CampaignOut): string {
  switch (campaign.status) {
    case "scheduled":
      return campaign.scheduled_at === null ? "—" : `Scheduled for ${formatInstant(campaign.scheduled_at)}`;
    case "sent":
      return formatInstant(campaign.completed_at ?? campaign.started_at ?? campaign.updated_at);
    case "sending":
    case "paused":
      return campaign.started_at === null ? "—" : `Started ${formatInstant(campaign.started_at)}`;
    case "cancelled":
      return campaign.cancelled_at === null ? "—" : `Cancelled ${formatInstant(campaign.cancelled_at)}`;
    case "draft":
      return "—";
  }
}

function CampaignRow({ campaign }: { campaign: CampaignOut }) {
  return (
    <tr className="border-t border-line align-top">
      <th scope="row" className="py-3 pr-4 text-left">
        <Link
          to={`/campaigns/${encodeURIComponent(campaign.id)}`}
          className="text-sm font-bold text-ink underline-offset-4 hover:text-accent-ink hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {campaign.name}
        </Link>
        {campaign.subject !== "" ? (
          <span className="mt-1 block text-xs text-muted">{campaign.subject}</span>
        ) : null}
      </th>
      <td className="py-3 pr-4">
        <CampaignStatusTag status={campaign.status} />
      </td>
      <td className="py-3 pr-4 text-sm tabular-nums text-ink">
        {formatNumber(campaign.stats.recipients)}
      </td>
      <td className="py-3 pr-4 text-sm tabular-nums text-ink">{formatNumber(campaign.stats.sent)}</td>
      <td className="py-3 pr-4 text-sm tabular-nums text-negative">
        {campaign.stats.failed > 0 ? formatNumber(campaign.stats.failed) : "—"}
      </td>
      <td className="py-3 pr-4 text-sm text-muted">{whenText(campaign)}</td>
    </tr>
  );
}

function NewCampaignButton() {
  const navigate = useNavigate();
  return (
    <Can permission="engage.campaign.campaign.write" scope="ORG">
      <Button onClick={() => navigate("/campaigns/new")}>Create Campaign</Button>
    </Can>
  );
}

function CampaignTable({ campaigns }: { campaigns: CampaignOut[] }) {
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-4xl border-collapse text-left">
        <caption className="sr-only">
          Campaigns, their status, recipients, how many were sent and failed, and when
        </caption>
        <thead>
          <tr className="text-xs font-bold uppercase tracking-wider text-faint">
            <th scope="col" className="pb-2 pr-4">
              Name
            </th>
            <th scope="col" className="pb-2 pr-4">
              Status
            </th>
            <th scope="col" className="pb-2 pr-4">
              Recipients
            </th>
            <th scope="col" className="pb-2 pr-4">
              Sent
            </th>
            <th scope="col" className="pb-2 pr-4">
              Failed
            </th>
            <th scope="col" className="pb-2 pr-4">
              Sent At
            </th>
          </tr>
        </thead>
        <tbody>
          {campaigns.map((campaign) => (
            <CampaignRow key={campaign.id} campaign={campaign} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EmailCampaignsPage() {
  const [tab, setTab] = useState<Tab>("all");
  const [query, setQuery] = useState("");

  const counts = useAsync(countCampaignsByStatus, []);
  const loader = useCallback(
    () => listCampaigns({ ...(tab === "all" ? {} : { status: tab }), page: 1, size: FULL_PAGE }),
    [tab],
  );
  const state = useAsync(loader, [tab]);

  const campaigns = state.status === "success" ? state.data.items : null;
  const shown = useMemo(() => {
    if (campaigns === null) return [];
    const needle = query.trim().toLowerCase();
    if (needle === "") return campaigns;
    return campaigns.filter((c) => `${c.name} ${c.subject}`.toLowerCase().includes(needle));
  }, [campaigns, query]);

  const items = tabItems(counts.status === "success" ? counts.data : null);
  const tabLabel = TAB_LABELS.find((t) => t.id === tab)?.label.toLowerCase() ?? "";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeading
        eyebrow="Email"
        title="Email Campaigns"
        description="Create and manage email campaigns for marketing and communication."
        actions={<NewCampaignButton />}
      />

      <div aria-live="polite" className="sr-only">
        {state.status === "loading" ? "Loading campaigns" : ""}
      </div>

      <Card className="flex flex-col gap-4 p-4 sm:p-6">
        <Tabs
          label="Campaign status"
          items={items}
          value={tab}
          onChange={(id) => {
            setTab(id as Tab);
            setQuery("");
          }}
          activation="manual"
        >
          <div className="flex flex-col gap-4 pt-4">
        <SearchInput
          label="Search campaigns"
          placeholder="Search campaigns..."
          containerClassName="sm:max-w-sm"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />

        {state.status === "loading" ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-32 w-full rounded-tile" />
          </div>
        ) : null}

        {state.status === "error" ? (
          <LoadFailure error={state.error} title="Couldn't load your campaigns" />
        ) : null}

        {state.status === "success" && campaigns !== null && campaigns.length === 0 ? (
          <EmptyState
            icon={<MegaphoneIcon className="size-6" />}
            title={tab === "all" ? "No campaigns yet" : `No ${tabLabel} campaigns`}
            description={
              tab === "all"
                ? "Draft a campaign once you have a usable sender and something to send it to."
                : "Campaigns in other statuses are under the other tabs."
            }
            {...(tab === "all" ? { action: <NewCampaignButton /> } : {})}
          />
        ) : null}

        {state.status === "success" && campaigns !== null && campaigns.length > 0 ? (
          <>
            {shown.length === 0 ? (
              <EmptyState
                icon={<MegaphoneIcon className="size-6" />}
                title="No campaigns match that search"
                description="Try part of a campaign name or subject, or clear the search."
                action={
                  <Button variant="secondary" onClick={() => setQuery("")}>
                    Clear search
                  </Button>
                }
              />
            ) : (
              <CampaignTable campaigns={shown} />
            )}
            <p className="text-xs text-muted">
              Showing {formatNumber(shown.length)} of {formatNumber(state.data.total)} campaigns
              {state.data.total > campaigns.length
                ? `. This is the newest ${formatNumber(campaigns.length)}; pick a status to narrow the rest.`
                : ""}
            </p>
          </>
        ) : null}
          </div>
        </Tabs>
      </Card>
    </div>
  );
}
