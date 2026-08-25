import { useMemo } from "react";

import { getCampaigns } from "@/api/client";
import type { Campaign, ChannelPerformance } from "@/api/types";
import { channelPerformance, overviewTotals } from "@/data/derive";
import { useAsync } from "@atmora/client";
import { cn } from "@atmora/ui";
import { formatMoney, formatMoneyCompact, formatNumber, formatRoas } from "@/lib/format";
import {
  Card,
  ChannelBadge,
  DataTable,
  ErrorState,
  PageHeader,
  SectionHeader,
  Skeleton,
  StatTile,
} from "@/ui";
import type { Column } from "@/ui";

const CURRENCY = "INR";
const NO_CAMPAIGNS: Campaign[] = [];

function roasClass(r: number): string {
  if (r >= 3) return "text-present";
  if (r >= 2) return "text-ink";
  return "text-negative";
}

const columns: Column<ChannelPerformance>[] = [
  {
    key: "channel",
    header: "Channel",
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
      <span className="tnum text-ink-soft">{formatMoneyCompact(r.spend_minor, CURRENCY)}</span>
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
        {r.cac_minor === null ? "—" : formatMoney(r.cac_minor, CURRENCY)}
      </span>
    ),
  },
  {
    key: "revenue",
    header: "Revenue",
    align: "right",
    render: (r) => (
      <span className="tnum font-semibold text-ink">
        {formatMoneyCompact(r.revenue_minor, CURRENCY)}
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

export function ChannelsPage() {
  const campaigns = useAsync(getCampaigns, []);
  const items = useMemo(
    () => (campaigns.status === "success" ? campaigns.data.items : NO_CAMPAIGNS),
    [campaigns],
  );
  const rows = useMemo(() => channelPerformance(items), [items]);
  const totals = useMemo(() => overviewTotals(items), [items]);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader eyebrow="Reach" title="Channels" />

      {/* Blended totals */}
      <section
        aria-label="Blended totals"
        className="grid grid-cols-2 gap-4 sm:grid-cols-4"
      >
        {campaigns.status === "success" ? (
          <>
            <StatTile
              label="Total spend"
              value={formatMoneyCompact(totals.spendMinor, CURRENCY)}
              sub="across all channels"
            />
            <StatTile
              label="Total revenue"
              value={formatMoneyCompact(totals.revenueMinor, CURRENCY)}
              sub="attributed"
            />
            <StatTile label="Blended ROAS" value={formatRoas(totals.roas)} sub="revenue ÷ spend" />
            <StatTile label="Leads" value={formatNumber(totals.leads)} sub="qualified" />
          </>
        ) : (
          Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-tile" />
          ))
        )}
      </section>

      <Card className="flex flex-col gap-4 p-6">
        <SectionHeader
          title="Performance by channel"
          subtitle="Ranked by attributed revenue"
        />
        {campaigns.status === "loading" ? (
          <div className="flex flex-col gap-2 py-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {campaigns.status === "error" ? <ErrorState message={campaigns.error.message} /> : null}
        {campaigns.status === "success" ? (
          <DataTable
            caption="Per-channel spend, leads, CAC, revenue and ROAS"
            columns={columns}
            rows={rows}
            rowKey={(r) => r.channel}
          />
        ) : null}
      </Card>
    </div>
  );
}
