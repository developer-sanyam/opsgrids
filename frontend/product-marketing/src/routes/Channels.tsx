import { useMemo } from "react";

import { getCampaigns, getChannels } from "@/api/client";
import type { ChannelPerformance } from "@/api/types";
import { problemMessage, useAsync } from "@opsgrids/client";
import { cn, MegaphoneIcon } from "@opsgrids/ui";
import { formatMoney, formatMoneyCompact, formatNumber, formatRoas } from "@/lib/format";
import {
  Card,
  ChannelBadge,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionHeader,
  Skeleton,
  StatTile,
} from "@/ui";
import type { Column } from "@/ui";

function roasClass(r: number): string {
  if (r >= 3) return "text-present";
  if (r >= 2) return "text-ink";
  return "text-negative";
}

export function ChannelsPage() {
  const channels = useAsync(getChannels, []);
  // `/marketing/channels` rolls up minor units but does not repeat the currency on every row —
  // the org has exactly one (the rollups assume it), so one bounded read of the campaign list is
  // enough to know what it is. Falls back to "INR" for an org with no campaigns yet.
  const currencyProbe = useAsync(() => getCampaigns({ size: 1 }), []);
  const currency =
    currencyProbe.status === "success" ? (currencyProbe.data.items[0]?.currency ?? "INR") : "INR";
  const rows = useMemo(
    () => (channels.status === "success" ? channels.data : []),
    [channels],
  );

  const totals = useMemo(() => {
    const spend = rows.reduce((sum, r) => sum + r.spend_minor, 0);
    const revenue = rows.reduce((sum, r) => sum + r.revenue_minor, 0);
    const leads = rows.reduce((sum, r) => sum + r.leads, 0);
    return { spend, revenue, leads, roas: spend === 0 ? 0 : revenue / spend };
  }, [rows]);

  const columns: Column<ChannelPerformance>[] = [
    {
      key: "channel",
      header: "Channel",
      mobile: "title",
      render: (r) => <ChannelBadge channel={r.channel} />,
    },
    {
      key: "campaigns",
      header: "Campaigns",
      align: "right",
      render: (r) => <span className="tnum text-ink-soft">{r.campaigns}</span>,
    },
    {
      key: "spend",
      header: "Spend",
      align: "right",
      render: (r) => (
        <span className="tnum text-ink-soft">{formatMoneyCompact(r.spend_minor, currency)}</span>
      ),
    },
    {
      key: "leads",
      header: "Leads",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (r) => <span className="tnum text-ink-soft">{formatNumber(r.leads)}</span>,
    },
    {
      key: "cac",
      header: "CAC",
      align: "right",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (r) => (
        <span className="tnum text-ink-soft">
          {r.cac_minor === null ? "—" : formatMoney(r.cac_minor, currency)}
        </span>
      ),
    },
    {
      key: "revenue",
      header: "Revenue",
      align: "right",
      render: (r) => (
        <span className="tnum font-semibold text-ink">
          {formatMoneyCompact(r.revenue_minor, currency)}
        </span>
      ),
    },
    {
      key: "roas",
      header: "ROAS",
      align: "right",
      render: (r) => (
        <span className={cn("tnum font-bold", roasClass(r.roas))}>{formatRoas(r.roas)}</span>
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader eyebrow="Reach" title="Channels" />

      {/* Blended totals */}
      <section aria-label="Blended totals" className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {channels.status === "success" ? (
          <>
            <StatTile
              label="Total spend"
              value={formatMoneyCompact(totals.spend, currency)}
              sub="across all channels"
            />
            <StatTile
              label="Total revenue"
              value={formatMoneyCompact(totals.revenue, currency)}
              sub="attributed"
            />
            <StatTile label="Blended ROAS" value={formatRoas(totals.roas)} sub="revenue ÷ spend" />
            <StatTile label="Leads" value={formatNumber(totals.leads)} sub="qualified" />
          </>
        ) : channels.status === "error" ? (
          <Card className="p-5 col-span-2 sm:col-span-4">
            <ErrorState
              title="Couldn't load channel totals"
              message={problemMessage(channels.error)}
              onRetry={channels.reload}
            />
          </Card>
        ) : (
          Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-tile" />
          ))
        )}
      </section>

      <Card className="flex flex-col gap-4 p-6">
        <SectionHeader title="Performance by channel" subtitle="Ranked by attributed revenue" />
        {channels.status === "loading" ? (
          <div className="flex flex-col gap-2 py-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {channels.status === "error" ? (
          <ErrorState message={problemMessage(channels.error)} onRetry={channels.reload} />
        ) : null}
        {channels.status === "success" ? (
          <DataTable
            caption="Per-channel spend, leads, CAC, revenue and ROAS"
            columns={columns}
            rows={rows}
            rowKey={(r) => r.channel}
            mobileCard="auto"
            emptyState={
              <EmptyState
                icon={<MegaphoneIcon className="size-6" />}
                title="No channel activity yet"
                description="This fills in once a campaign is spending on a channel."
              />
            }
          />
        ) : null}
      </Card>
    </div>
  );
}
