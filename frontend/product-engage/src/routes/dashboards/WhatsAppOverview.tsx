import { useAsync } from "@opsgrids/client";
import {
  AlertOctagonIcon,
  BanknoteIcon,
  CheckCircleIcon,
  EyeIcon,
  FileTextIcon,
  MessageIcon,
  MessageSquareIcon,
  PageHeader,
  SendIcon,
  SparkIcon,
} from "@opsgrids/ui";

import { getWhatsAppOverview } from "@/api/analytics";
import type { WhatsAppOverview } from "@/api/analytics";
import { formatNumber } from "@/lib/format";
import { ActivityCard, KpiGrid, LoadingStatus } from "./DashboardParts";
import type { Kpi } from "./DashboardParts";
import { DashboardFailure } from "./DashboardFailure";
import { DateRangeControl } from "./DateRangeControl";
import { SectionTabs } from "./SectionTabs";
import { VolumeChartCard } from "./VolumeChartCard";
import { formatMoney, shareCaption } from "./format";
import { useDateRange } from "./useDateRange";

/**
 * The WhatsApp overview (`GET /engage/analytics/whatsapp/overview`): the five headline counts, the
 * per-day volume, what just happened, and what the workspace holds. Every figure is the service's;
 * the only arithmetic here is a share of two counts on the same response ("92.8% of sent"), and it
 * is left out when nothing was sent rather than shown as zero.
 *
 * **The cost is an estimate and says so in its own label.** The service multiplies message counts
 * by a rate card, not by what Meta bills, so the figure is never presented as a charge.
 *
 * There is no "vs last period" arrow on any card: the endpoint returns one period, and a trend the
 * client would have to fetch a second period to draw is not one this screen makes up.
 */

function whatsAppKpis(data: WhatsAppOverview): Kpi[] {
  return [
    {
      key: "sent",
      label: "Messages sent",
      value: formatNumber(data.sent),
      icon: <SendIcon className="size-5" />,
      tone: "accent",
    },
    {
      key: "delivered",
      label: "Delivered",
      value: formatNumber(data.delivered),
      icon: <CheckCircleIcon className="size-5" />,
      tone: "positive",
      caption: shareCaption(data.delivered, data.sent, "sent"),
    },
    {
      key: "read",
      label: "Read",
      value: formatNumber(data.read),
      icon: <EyeIcon className="size-5" />,
      tone: "cyan",
      caption: shareCaption(data.read, data.sent, "sent"),
    },
    {
      key: "replies",
      label: "Responses",
      value: formatNumber(data.replies),
      icon: <MessageIcon className="size-5" />,
      tone: "info",
      caption: shareCaption(data.replies, data.sent, "sent"),
    },
    {
      key: "failed",
      label: "Failed",
      value: formatNumber(data.failed),
      icon: <AlertOctagonIcon className="size-5" />,
      tone: "danger",
      caption: shareCaption(data.failed, data.sent, "sent"),
    },
  ];
}

function totalsKpis(data: WhatsAppOverview): Kpi[] {
  return [
    {
      key: "automations",
      label: "Active automations",
      value: formatNumber(data.totals.active_automations),
      icon: <SparkIcon className="size-5" />,
      tone: "positive",
    },
    {
      key: "templates",
      label: "Approved templates",
      value: formatNumber(data.totals.approved_templates),
      icon: <FileTextIcon className="size-5" />,
      tone: "accent",
      caption: `${formatNumber(data.totals.pending_templates)} pending`,
    },
    {
      key: "conversations",
      label: "Active conversations",
      value: formatNumber(data.totals.active_conversations),
      icon: <MessageSquareIcon className="size-5" />,
      tone: "info",
      caption: `${formatNumber(data.totals.unassigned_conversations)} unassigned`,
    },
    {
      key: "cost",
      label: "Estimated WhatsApp cost",
      value: formatMoney(data.cost.total),
      icon: <BanknoteIcon className="size-5" />,
      tone: "warning",
      caption: "Estimated from the rate card, not a bill",
    },
  ];
}

export function WhatsAppOverviewPage() {
  const dates = useDateRange();
  const { from, to } = dates.range;
  const state = useAsync(() => getWhatsAppOverview({ from, to }), [from, to]);
  const data = state.status === "success" ? state.data : null;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Communication"
        title="WhatsApp"
        description="Manage conversations, templates, automations and analytics."
        actions={<DateRangeControl controls={dates} />}
      />
      <SectionTabs section="whatsapp" />
      <LoadingStatus status={state.status} what="WhatsApp overview" />

      {state.status === "error" ? (
        <DashboardFailure
          error={state.error}
          title="Couldn't load the WhatsApp overview"
          what="the WhatsApp overview"
          onRetry={state.reload}
        />
      ) : (
        <>
          <KpiGrid
            label="Message totals"
            kpis={data === null ? null : whatsAppKpis(data)}
            columns={5}
          />

          <div className="grid gap-4 lg:grid-cols-3">
            <VolumeChartCard
              className="lg:col-span-2"
              title="Message volume"
              description="Messages per day in the selected range"
              chartLabel="WhatsApp messages per day: sent, delivered, read and failed"
              dates={data?.series.map((point) => point.date) ?? []}
              series={[
                { key: "sent", label: "Sent", tone: "accent", values: data?.series.map((p) => p.sent) ?? [] },
                {
                  key: "delivered",
                  label: "Delivered",
                  tone: "positive",
                  values: data?.series.map((p) => p.delivered) ?? [],
                },
                { key: "read", label: "Read", tone: "cyan", values: data?.series.map((p) => p.read) ?? [] },
                {
                  key: "failed",
                  label: "Failed",
                  tone: "danger",
                  values: data?.series.map((p) => p.failed) ?? [],
                },
              ]}
              loading={data === null}
              emptyTitle="No messages in this period"
              emptyDescription="Messages you send from Engage appear here. Try a wider date range."
            />
            <ActivityCard
              items={data?.recent_activity ?? []}
              loading={data === null}
              viewAll={{ to: "/whatsapp/inbox", label: "View all" }}
              emptyDescription="Deliveries, replies, automation runs and template reviews appear here as they happen."
            />
          </div>

          <KpiGrid
            label="Workspace totals"
            kpis={data === null ? null : totalsKpis(data)}
            columns={4}
            compact
          />
        </>
      )}
    </div>
  );
}
