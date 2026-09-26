import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import {
  ArrowLeftIcon,
  BarChartIcon,
  BarList,
  Card,
  CheckCircleIcon,
  ChartCard,
  DonutChart,
  EmptyState,
  EyeIcon,
  MinusCircleIcon,
  PageHeader,
  TargetIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { BarListItem, DonutSlice } from "@opsgrids/ui";

import { getCampaignAnalytics } from "@/api/analytics";
import type { CampaignAnalytics } from "@/api/analytics";
import { getCampaign } from "@/api/client";
import type { CampaignOut } from "@/api/types";
import { formatInstant, formatNumber } from "@/lib/format";
import { CampaignStatusTag } from "@/ui";
import { ActionLink, KpiGrid, LoadingStatus } from "./DashboardParts";
import type { Kpi } from "./DashboardParts";
import { DashboardFailure } from "./DashboardFailure";
import { formatDay } from "./dates";
import { deviceLabel, linkLabel, percentOf, shareCaption } from "./format";
import { TrendChart } from "./TrendChart";

/**
 * One campaign's analytics (`GET /engage/campaigns/{id}/analytics`).
 *
 * **"Off" is not "zero".** Tracking is off by default per campaign: with no pixel and no rewritten
 * links, nobody could have been counted opening or clicking. So when a flag is off, every place that
 * would show that number shows "Tracking is off for this campaign" instead — a card of zeros would
 * read as a campaign nobody opened, which is a claim the service never measured. When the flag is on
 * and the count really is zero, the card says "No opens recorded yet", which is a different fact.
 *
 * Rates are worked out here from the counts on the same response, over recipients, because the
 * response's own `open_rate` and `click_rate` do not say whether they are a fraction or a percentage
 * and a wrong guess prints 100 times too small.
 */

const OPENS_OFF_TITLE = "Tracking is off for this campaign";

function TrackingOff({ what }: { what: string }) {
  return (
    <EmptyState
      icon={<EyeIcon className="size-6" />}
      title={OPENS_OFF_TITLE}
      description={`${what} Delivery, bounces and unsubscribes are still reported.`}
    />
  );
}

function kpis(data: CampaignAnalytics): Kpi[] {
  const { opens, clicks } = data.tracking;
  return [
    {
      key: "recipients",
      label: "Recipients",
      value: formatNumber(data.recipients),
      icon: <UsersIcon className="size-5" />,
      tone: "accent",
    },
    {
      key: "delivered",
      label: "Delivered",
      value: formatNumber(data.delivered),
      icon: <CheckCircleIcon className="size-5" />,
      tone: "positive",
      caption: shareCaption(data.delivered, data.recipients, "recipients"),
    },
    {
      key: "opened",
      label: "Opened",
      value: opens ? formatNumber(data.opened) : "Off",
      icon: <EyeIcon className="size-5" />,
      tone: "info",
      caption: opens ? shareCaption(data.opened, data.recipients, "recipients") : OPENS_OFF_TITLE,
    },
    {
      key: "clicked",
      label: "Clicked",
      value: clicks ? formatNumber(data.clicked) : "Off",
      icon: <TargetIcon className="size-5" />,
      tone: "cyan",
      caption: clicks ? shareCaption(data.clicked, data.recipients, "recipients") : OPENS_OFF_TITLE,
    },
    {
      key: "unsubscribed",
      label: "Unsubscribed",
      value: formatNumber(data.unsubscribed),
      icon: <MinusCircleIcon className="size-5" />,
      tone: "warning",
      caption: shareCaption(data.unsubscribed, data.recipients, "recipients"),
    },
  ];
}

function OpensByDevice({ data }: { data: CampaignAnalytics }) {
  if (!data.tracking.opens) {
    return (
      <TrackingOff what="Open tracking was not turned on when this campaign was sent, so opens weren't counted." />
    );
  }
  const slices: DonutSlice[] = data.by_device.map((row) => ({
    key: row.device,
    label: deviceLabel(row.device),
    value: row.opens,
  }));
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  if (total === 0) {
    return (
      <EmptyState
        icon={<EyeIcon className="size-6" />}
        title="No opens recorded yet"
        description="Opens appear here as recipients read the email. Automated opens are left out."
      />
    );
  }
  return (
    <DonutChart
      label="Opens by device"
      slices={slices}
      formatValue={formatNumber}
      centerLabel="Opens"
      layout="stacked"
    />
  );
}

function ClicksByLink({ data }: { data: CampaignAnalytics }) {
  if (!data.tracking.clicks) {
    return (
      <TrackingOff what="Click tracking was not turned on when this campaign was sent, so clicks weren't counted." />
    );
  }
  const items: BarListItem[] = data.by_link.map((row) => ({
    key: row.url,
    label: linkLabel(row.url),
    value: row.clicks,
  }));
  if (items.every((item) => item.value === 0)) {
    return (
      <EmptyState
        icon={<TargetIcon className="size-6" />}
        title="No clicks recorded yet"
        description="Links appear here, ranked, as recipients click them."
      />
    );
  }
  return <BarList label="Clicks by link" items={items} formatValue={formatNumber} />;
}

function EngagementOverTime({ data }: { data: CampaignAnalytics }) {
  const { opens, clicks } = data.tracking;
  if (!opens && !clicks) {
    return (
      <TrackingOff what="Neither open nor click tracking was turned on when this campaign was sent, so there is no engagement to chart." />
    );
  }
  const series = [
    ...(opens
      ? [{ key: "opens", label: "Opens", tone: "accent" as const, values: data.series.map((p) => p.opens) }]
      : []),
    ...(clicks
      ? [{ key: "clicks", label: "Clicks", tone: "positive" as const, values: data.series.map((p) => p.clicks) }]
      : []),
  ];
  if (data.series.length === 0 || series.every((one) => one.values.every((value) => value === 0))) {
    return (
      <EmptyState
        icon={<BarChartIcon className="size-6" />}
        title="No engagement recorded yet"
        description="Opens and clicks are charted per day once recipients start engaging."
      />
    );
  }
  return (
    <TrendChart
      label={`Engagement per day: ${series.map((one) => one.label.toLowerCase()).join(" and ")}`}
      categories={data.series.map((point) => formatDay(point.date))}
      series={series}
      height="md"
    />
  );
}

function DeliveryRow({
  label,
  hint,
  value,
  share,
}: {
  label: string;
  hint: string;
  value: number;
  share: string | null;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-t border-line py-3 first:border-t-0 first:pt-0">
      <div className="flex min-w-0 flex-col">
        <dt className="text-sm font-semibold text-ink">{label}</dt>
        <dd className="text-xs text-muted">{hint}</dd>
      </div>
      <dd className="flex shrink-0 flex-col items-end">
        <span className="tnum text-base font-bold text-ink">{formatNumber(value)}</span>
        {share !== null ? <span className="tnum text-xs text-muted">{share} of recipients</span> : null}
      </dd>
    </div>
  );
}

function Deliverability({ data }: { data: CampaignAnalytics }) {
  return (
    <div className="flex flex-col gap-4">
      <dl className="flex flex-col">
        <DeliveryRow
          label="Delivered"
          hint="Accepted by your email provider"
          value={data.delivered}
          share={percentOf(data.delivered, data.recipients)}
        />
        <DeliveryRow
          label="Bounced"
          hint="Refused by the recipient's server"
          value={data.bounced}
          share={percentOf(data.bounced, data.recipients)}
        />
        <DeliveryRow
          label="Spam complaints"
          hint="Marked as spam by the recipient"
          value={data.complaints}
          share={percentOf(data.complaints, data.recipients)}
        />
        <DeliveryRow
          label="Unsubscribed"
          hint="Opted out of marketing email"
          value={data.unsubscribed}
          share={percentOf(data.unsubscribed, data.recipients)}
        />
      </dl>
      <p className="text-xs text-muted">
        Bounces and complaints are read from your connected mailbox, so they can be under-counted
        until mailbox sync is turned on.
      </p>
    </div>
  );
}

function CampaignDetails({ campaign }: { campaign: CampaignOut }) {
  const sentAt = campaign.started_at ?? campaign.scheduled_at;
  return (
    <dl className="flex flex-col">
      {[
        { label: "Subject", value: <>{campaign.subject}</> },
        { label: "Status", value: <CampaignStatusTag status={campaign.status} /> },
        {
          label: campaign.started_at !== null ? "Sent" : "Scheduled for",
          value: <>{sentAt === null ? "Not sent yet" : formatInstant(sentAt)}</>,
        },
      ].map((row: { label: string; value: ReactNode }) => (
        <div
          key={row.label}
          className="grid grid-cols-3 gap-3 border-t border-line py-3 first:border-t-0 first:pt-0"
        >
          <dt className="text-sm text-muted">{row.label}</dt>
          <dd className="col-span-2 text-sm font-semibold text-ink">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Content({ data, campaign }: { data: CampaignAnalytics | null; campaign: CampaignOut | null }) {
  const loading = data === null;

  if (data !== null && data.recipients === 0) {
    return (
      <Card className="p-6">
        <EmptyState
          icon={<UsersIcon className="size-6" />}
          title="Nothing to report yet"
          description="This campaign has not reached any recipients, so there are no figures to show. Analytics appear once it has sent."
        />
      </Card>
    );
  }

  return (
    <>
      <KpiGrid label="Campaign totals" kpis={data === null ? null : kpis(data)} columns={5} />

      <div className="grid gap-4 lg:grid-cols-3">
        <ChartCard title="Opens by device" loading={loading} skeleton="donut">
          {data !== null ? <OpensByDevice data={data} /> : null}
        </ChartCard>
        <ChartCard title="Clicks by link" loading={loading} skeleton="bar">
          {data !== null ? <ClicksByLink data={data} /> : null}
        </ChartCard>
        <ChartCard title="Engagement over time" description="Per day" loading={loading}>
          {data !== null ? <EngagementOverTime data={data} /> : null}
        </ChartCard>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Deliverability" loading={loading} skeleton="bar">
          {data !== null ? <Deliverability data={data} /> : null}
        </ChartCard>
        {campaign !== null ? (
          <ChartCard title="Campaign details">
            <CampaignDetails campaign={campaign} />
          </ChartCard>
        ) : null}
      </div>
    </>
  );
}

export function CampaignAnalyticsPage() {
  const { id: campaignId = "" } = useParams();
  const analytics = useAsync(() => getCampaignAnalytics(campaignId), [campaignId]);
  // The header and the details card borrow the campaign itself. If that read fails the analytics
  // still stand on their own, so its failure is not surfaced as one.
  const campaignState = useAsync(() => getCampaign(campaignId), [campaignId]);
  const campaign = campaignState.status === "success" ? campaignState.data : null;

  const sentAt = campaign?.started_at ?? null;

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/campaigns"
        className="inline-flex w-fit items-center gap-2 rounded-tile text-sm font-semibold text-accent-ink hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <ArrowLeftIcon className="size-4" />
        Campaigns
      </Link>
      <PageHeader
        eyebrow="Campaign analytics"
        title={campaign?.name ?? "Campaign analytics"}
        description={
          sentAt !== null
            ? `Sent on ${formatInstant(sentAt)}`
            : "Opens, clicks and deliverability for this campaign."
        }
        actions={<ActionLink to={`/campaigns/${encodeURIComponent(campaignId)}`} variant="secondary">View campaign</ActionLink>}
      />
      <LoadingStatus status={analytics.status} what="campaign analytics" />

      {analytics.status === "error" ? (
        <DashboardFailure
          error={analytics.error}
          title="Couldn't load campaign analytics"
          what="this campaign's analytics"
          onRetry={analytics.reload}
        />
      ) : (
        <Content data={analytics.status === "success" ? analytics.data : null} campaign={campaign} />
      )}
    </div>
  );
}
