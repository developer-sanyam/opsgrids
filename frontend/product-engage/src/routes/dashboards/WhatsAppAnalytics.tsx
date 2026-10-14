import { useAsync } from "@opsgrids/client";
import {
  AlertOctagonIcon,
  BanknoteIcon,
  BarChartIcon,
  BarList,
  ChartCard,
  CheckCircleIcon,
  DonutChart,
  EmptyState,
  PageHeader,
  Tag,
} from "@opsgrids/ui";
import type { BarListItem, DonutSlice, Tone } from "@opsgrids/ui";

import { getWhatsAppOverview } from "@/api/analytics";
import type { MessagingCost, WhatsAppOverview } from "@/api/analytics";
import { formatNumber } from "@/lib/format";
import { LoadingStatus } from "./DashboardParts";
import { DashboardFailure } from "./DashboardFailure";
import { DateRangeControl, DateRangeSummary } from "./DateRangeControl";
import { SectionTabs } from "./SectionTabs";
import { VolumeChartCard } from "./VolumeChartCard";
import { formatMoney } from "./format";
import { useDateRange } from "./useDateRange";

/**
 * WhatsApp analytics: the same read as the overview, taken apart — how each message ended up, what
 * kind of conversation it opened, why the failures failed, and what the period cost.
 *
 * Every chart here is a `@opsgrids/ui` chart, and each of them is also a table for a screen
 * reader (item, value, share) — the visible legend restates the numbers, it does not replace them.
 * The cost is labelled **Estimated** in its heading, in its total and in its note: it is message
 * counts times a rate card, and Meta's own invoice is what is actually owed.
 */

const STATUS: Record<WhatsAppOverview["status_breakdown"][number]["status"], { label: string; tone: Tone }> = {
  read: { label: "Read", tone: "positive" },
  delivered: { label: "Delivered", tone: "info" },
  sent: { label: "Sent", tone: "accent" },
  failed: { label: "Failed", tone: "danger" },
};

const CATEGORY: Record<string, string> = {
  marketing: "Marketing",
  utility: "Utility",
  authentication: "Authentication",
  service: "Service",
};

function categoryLabel(category: string): string {
  return CATEGORY[category] ?? `${category.charAt(0).toUpperCase()}${category.slice(1)}`;
}

function EstimatedCostTable({ cost }: { cost: MessagingCost }) {
  return (
    <div className="flex flex-col gap-4">
      <table className="w-full text-sm">
        <caption className="sr-only">Estimated WhatsApp cost by category</caption>
        <thead>
          <tr className="text-left text-xs font-bold uppercase tracking-wider text-faint">
            <th scope="col" className="py-2 pr-4 font-bold">
              Category
            </th>
            <th scope="col" className="py-2 pr-4 text-right font-bold">
              Messages
            </th>
            <th scope="col" className="py-2 text-right font-bold">
              Estimated cost
            </th>
          </tr>
        </thead>
        <tbody>
          {cost.by_category.map((row) => (
            <tr key={row.category} className="border-t border-line">
              <th scope="row" className="py-3 pr-4 text-left font-medium text-ink">
                {categoryLabel(row.category)}
              </th>
              <td className="tnum py-3 pr-4 text-right text-ink">{formatNumber(row.messages)}</td>
              <td className="tnum py-3 text-right text-ink">{formatMoney(row.amount)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t border-line">
            <th scope="row" className="py-3 pr-4 text-left font-bold text-ink">
              Total
            </th>
            <td className="py-3 pr-4" />
            <td className="tnum py-3 text-right font-bold text-ink">{formatMoney(cost.total)}</td>
          </tr>
        </tfoot>
      </table>
      {cost.by_source !== undefined && cost.by_source.length > 0 ? (
        <table className="w-full text-sm">
          <caption className="sr-only">Estimated WhatsApp cost by automation and conversation</caption>
          <thead>
            <tr className="text-left text-xs font-bold uppercase tracking-wider text-faint">
              <th scope="col" className="py-2 pr-4 font-bold">
                Sent by
              </th>
              <th scope="col" className="py-2 pr-4 text-right font-bold">
                Messages
              </th>
              <th scope="col" className="py-2 text-right font-bold">
                Estimated cost
              </th>
            </tr>
          </thead>
          <tbody>
            {cost.by_source.map((row) => (
              <tr key={`${row.kind}:${row.key}`} className="border-t border-line">
                <th scope="row" className="py-3 pr-4 text-left font-medium text-ink">
                  <span className="text-muted">{row.kind === "automation" ? "Automation" : "Conversation"}</span>{" "}
                  {row.label}
                </th>
                <td className="tnum py-3 pr-4 text-right text-ink">{formatNumber(row.messages)}</td>
                <td className="tnum py-3 text-right text-ink">{formatMoney(row.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <p className="text-xs text-muted">
        Estimated at Meta&rsquo;s list rate for each recipient&rsquo;s country, not from Meta&rsquo;s invoice
        (volume discounts and tax are not applied).
        {cost.priced_by_meta !== undefined
          ? ` ${formatNumber(cost.priced_by_meta)} of ${formatNumber(
              cost.by_category.reduce((sum, row) => sum + row.messages, 0),
            )} messages were priced from Meta’s own delivery receipts; the rest by their template’s category.`
          : null}
      </p>
    </div>
  );
}

function Content({ data }: { data: WhatsAppOverview | null }) {
  const loading = data === null;

  const statusSlices: DonutSlice[] =
    data?.status_breakdown.map((row) => ({
      key: row.status,
      label: STATUS[row.status].label,
      value: row.count,
      tone: STATUS[row.status].tone,
    })) ?? [];
  const statusTotal = statusSlices.reduce((sum, slice) => sum + slice.value, 0);

  const categories: BarListItem[] =
    data?.by_category.map((row) => ({
      key: row.category,
      label: categoryLabel(row.category),
      value: row.messages,
    })) ?? [];
  const categoryTotal = categories.reduce((sum, item) => sum + item.value, 0);

  const failures: BarListItem[] =
    data?.failures_by_reason.map((row) => ({ key: row.code, label: row.label, value: row.count })) ?? [];

  return (
    <>
      <VolumeChartCard
        title="Message volume"
        description="Messages per day in the selected range"
        chartLabel="WhatsApp messages per day: sent, delivered, read and failed"
        dates={data?.series.map((point) => point.date) ?? []}
        series={[
          { key: "sent", label: "Sent", tone: "accent", values: data?.series.map((p) => p.sent) ?? [] },
          { key: "delivered", label: "Delivered", tone: "positive", values: data?.series.map((p) => p.delivered) ?? [] },
          { key: "read", label: "Read", tone: "cyan", values: data?.series.map((p) => p.read) ?? [] },
          { key: "failed", label: "Failed", tone: "danger", values: data?.series.map((p) => p.failed) ?? [] },
        ]}
        loading={loading}
        emptyTitle="No messages in this period"
        emptyDescription="Messages you send from Engage appear here. Try a wider date range."
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <ChartCard
          title="Message status"
          description="Where each message ended up"
          loading={loading}
          skeleton="donut"
          empty={
            statusTotal === 0 ? (
              <EmptyState
                icon={<CheckCircleIcon className="size-6" />}
                title="No messages to break down"
                description="Once messages are sent in this range, their delivery outcomes appear here."
              />
            ) : undefined
          }
        >
          <DonutChart
            label="WhatsApp messages by status"
            slices={statusSlices}
            formatValue={formatNumber}
            centerLabel="Messages"
            layout="stacked"
          />
        </ChartCard>

        <ChartCard
          title="Messages by category"
          description="Meta's conversation categories"
          loading={loading}
          skeleton="bar"
          empty={
            categoryTotal === 0 ? (
              <EmptyState
                icon={<BarChartIcon className="size-6" />}
                title="No categorised messages"
                description="Marketing, utility, authentication and service messages are counted here once they are sent."
              />
            ) : undefined
          }
        >
          <BarList label="WhatsApp messages by category" items={categories} formatValue={formatNumber} />
        </ChartCard>

        <ChartCard
          title="Failures by reason"
          description="Why messages were not delivered"
          loading={loading}
          skeleton="bar"
          empty={
            failures.length === 0 ? (
              <EmptyState
                icon={<AlertOctagonIcon className="size-6" />}
                title="No failed messages"
                description="Nothing failed in this range. Reasons appear here if a message is refused."
              />
            ) : undefined
          }
        >
          <BarList
            label="WhatsApp failures by reason"
            items={failures}
            formatValue={formatNumber}
            tone="danger"
          />
        </ChartCard>
      </div>

      <ChartCard
        title="Estimated cost by category"
        description="Message counts multiplied by the rate card"
        action={<Tag tone="warning">Estimated</Tag>}
        loading={loading}
        skeleton="bar"
        empty={
          data !== null && data.cost.by_category.length === 0 ? (
            <EmptyState
              icon={<BanknoteIcon className="size-6" />}
              title="No estimated cost for this period"
              description="An estimate appears once messages are sent in the selected range."
            />
          ) : undefined
        }
      >
        {data !== null ? <EstimatedCostTable cost={data.cost} /> : null}
      </ChartCard>
    </>
  );
}

export function WhatsAppAnalyticsPage() {
  const dates = useDateRange();
  const { from, to } = dates.range;
  const state = useAsync(() => getWhatsAppOverview({ from, to }), [from, to]);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Communication"
        title="WhatsApp analytics"
        description="Delivery outcomes, message categories, failures and estimated cost for the selected period."
        meta={<DateRangeSummary controls={dates} />}
        actions={<DateRangeControl controls={dates} />}
      />
      <SectionTabs section="whatsapp" />
      <LoadingStatus status={state.status} what="WhatsApp analytics" />

      {state.status === "error" ? (
        <DashboardFailure
          error={state.error}
          title="Couldn't load WhatsApp analytics"
          what="WhatsApp analytics"
          onRetry={state.reload}
        />
      ) : (
        <Content data={state.status === "success" ? state.data : null} />
      )}
    </div>
  );
}
