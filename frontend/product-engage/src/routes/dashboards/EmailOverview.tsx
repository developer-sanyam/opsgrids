import { useAsync } from "@opsgrids/client";
import {
  AlertOctagonIcon,
  CheckCircleIcon,
  EyeIcon,
  FileTextIcon,
  MailIcon,
  PageHeader,
  PlugIcon,
  SparkIcon,
  TargetIcon,
  UsersIcon,
} from "@opsgrids/ui";

import { getEmailOverview } from "@/api/analytics";
import type { EmailOverview } from "@/api/analytics";
import { formatNumber } from "@/lib/format";
import { ActionLink, ActivityCard, KpiGrid, LoadingStatus } from "./DashboardParts";
import type { Kpi } from "./DashboardParts";
import { DashboardFailure } from "./DashboardFailure";
import { DateRangeControl } from "./DateRangeControl";
import { SectionTabs } from "./SectionTabs";
import { VolumeChartCard } from "./VolumeChartCard";
import { shareCaption } from "./format";
import { useDateRange } from "./useDateRange";

/**
 * The email overview (`GET /engage/analytics/email/overview`).
 *
 * **"Delivered" is what the provider accepted**, not what reached an inbox: Engage sends through
 * the customer's own provider and learns no more than that. **Opens and clicks exist only for
 * campaigns that turned tracking on** — tracking is off by default — so the figures are a floor,
 * and a note under the cards says so instead of letting a low open rate read as a bad campaign.
 */

function emailKpis(data: EmailOverview): Kpi[] {
  return [
    {
      key: "sent",
      label: "Emails sent",
      value: formatNumber(data.sent),
      icon: <MailIcon className="size-5" />,
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
      key: "opened",
      label: "Opened",
      value: formatNumber(data.opened),
      icon: <EyeIcon className="size-5" />,
      tone: "info",
      caption: shareCaption(data.opened, data.delivered, "delivered"),
    },
    {
      key: "clicked",
      label: "Clicked",
      value: formatNumber(data.clicked),
      icon: <TargetIcon className="size-5" />,
      tone: "cyan",
      caption: shareCaption(data.clicked, data.delivered, "delivered"),
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

function totalsKpis(data: EmailOverview): Kpi[] {
  return [
    {
      key: "accounts",
      label: "Connected accounts",
      value: formatNumber(data.totals.connected_accounts),
      icon: <PlugIcon className="size-5" />,
      tone: "accent",
    },
    {
      key: "automations",
      label: "Active automations",
      value: formatNumber(data.totals.active_automations),
      icon: <SparkIcon className="size-5" />,
      tone: "positive",
    },
    {
      key: "templates",
      label: "Email templates",
      value: formatNumber(data.totals.templates),
      icon: <FileTextIcon className="size-5" />,
      tone: "info",
    },
    {
      key: "contacts",
      label: "Contacts",
      value: formatNumber(data.totals.contacts),
      icon: <UsersIcon className="size-5" />,
      tone: "warning",
      caption: `${formatNumber(data.totals.subscribed_contacts)} subscribed`,
    },
  ];
}

export function EmailOverviewPage() {
  const dates = useDateRange();
  const { from, to } = dates.range;
  const state = useAsync(() => getEmailOverview({ from, to }), [from, to]);
  const data = state.status === "success" ? state.data : null;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Communication"
        title="Email"
        description="Manage email accounts, templates, automations and track performance."
        actions={
          <>
            <DateRangeControl controls={dates} />
            <ActionLink to="/campaigns/new">Create campaign</ActionLink>
          </>
        }
      />
      <SectionTabs section="email" />
      <LoadingStatus status={state.status} what="email overview" />

      {state.status === "error" ? (
        <DashboardFailure
          error={state.error}
          title="Couldn't load the email overview"
          what="the email overview"
          onRetry={state.reload}
        />
      ) : (
        <>
          <KpiGrid label="Email totals" kpis={data === null ? null : emailKpis(data)} columns={5} />
          <p className="text-xs text-muted">
            &ldquo;Delivered&rdquo; means your provider accepted the message. Opens and clicks are
            counted only for campaigns that have tracking turned on.
          </p>

          <div className="grid gap-4 lg:grid-cols-3">
            <VolumeChartCard
              className="lg:col-span-2"
              title="Email performance"
              description="Emails per day in the selected range"
              chartLabel="Emails per day: sent, delivered, opened, clicked and failed"
              dates={data?.series.map((point) => point.date) ?? []}
              series={[
                { key: "sent", label: "Sent", tone: "accent", values: data?.series.map((p) => p.sent) ?? [] },
                {
                  key: "delivered",
                  label: "Delivered",
                  tone: "positive",
                  values: data?.series.map((p) => p.delivered) ?? [],
                },
                { key: "opened", label: "Opened", tone: "info", values: data?.series.map((p) => p.opened) ?? [] },
                { key: "clicked", label: "Clicked", tone: "cyan", values: data?.series.map((p) => p.clicked) ?? [] },
                { key: "failed", label: "Failed", tone: "danger", values: data?.series.map((p) => p.failed) ?? [] },
              ]}
              loading={data === null}
              emptyTitle="No emails in this period"
              emptyDescription="Emails you send from Engage appear here. Try a wider date range."
            />
            <ActivityCard
              items={data?.recent_activity ?? []}
              loading={data === null}
              viewAll={{ to: "/email/inbox", label: "View all" }}
              emptyDescription="Campaign sends, high open rates, new subscribers and automation runs appear here."
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
