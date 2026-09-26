import { useCallback, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import {
  ActivityIcon,
  ArrowLeftIcon,
  BarList,
  CheckCircleIcon,
  ClockIcon,
  KpiCard,
  LineChart,
  SparkIcon,
} from "@opsgrids/ui";

import { getStats } from "@/api/automations";
import type { StatsOut } from "@/api/automations";
import { formatInstant, formatNumber } from "@/lib/format";
import { Announcer, Button, Card, EmptyState, PageHeading, Skeleton, TextField } from "@/ui";
import { formatDurationMs, formatRate, ratePercent } from "./format";
import { LoadError } from "./LoadError";

/**
 * How the automations are performing, and where they fail. Every figure is the service's: the
 * success rate is its `successful` over its `executions`, the failure reasons are its own codes
 * with its own labels, and the chart plots what it sent, day by day. The screen only lays them out
 * and never recomputes one.
 *
 * The chart draws successful and failed as two lines and is also a table for anyone who cannot
 * read a chart, and the range is two date fields, so the whole screen works from the keyboard.
 */

function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function defaultRange(): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to.getTime() - 29 * 86_400_000);
  return { from: isoDay(from), to: isoDay(to) };
}

function shortDate(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" });
}

function Loading() {
  return (
    <div className="flex flex-col gap-6" aria-busy="true">
      <span className="sr-only" role="status">
        Loading analytics
      </span>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((n) => (
          <Skeleton key={n} className="h-32 w-full rounded-card" />
        ))}
      </div>
      <Skeleton className="h-64 w-full rounded-card" />
    </div>
  );
}

function Populated({ stats }: { stats: StatsOut }) {
  const failedShare = stats.executions === 0 ? null : stats.failed / stats.executions;
  const reasonsTotal = stats.failures_by_reason.reduce((sum, r) => sum + r.count, 0);
  return (
    <div className="flex flex-col gap-6">
      <section aria-label="Totals" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          label="Total executions"
          value={formatNumber(stats.executions)}
          icon={<SparkIcon className="size-5" />}
          tone="info"
        />
        <KpiCard
          label="Successful"
          value={formatNumber(stats.successful)}
          caption={stats.executions === 0 ? "No runs in this range" : `${formatRate(stats.successful / stats.executions)} success rate`}
          icon={<CheckCircleIcon className="size-5" />}
          tone="positive"
        />
        <KpiCard
          label="Failed"
          value={formatNumber(stats.failed)}
          caption={failedShare === null ? "No runs in this range" : `${formatRate(failedShare)} of executions`}
          icon={<ActivityIcon className="size-5" />}
          tone="danger"
        />
        <KpiCard
          label="Avg. execution time"
          value={formatDurationMs(stats.avg_duration_ms)}
          icon={<ClockIcon className="size-5" />}
          tone="accent"
        />
      </section>

      <div className="grid items-start gap-6 lg:grid-cols-3">
        <Card className="flex flex-col gap-4 p-4 sm:p-6 lg:col-span-2">
          <h2 className="text-lg font-bold">Executions over time</h2>
          {stats.series.length === 0 ? (
            <p className="text-sm text-muted">No executions in this range.</p>
          ) : (
            <LineChart
              label="Successful and failed executions per day"
              categories={stats.series.map((p) => shortDate(p.date))}
              series={[
                { key: "successful", label: "Successful", values: stats.series.map((p) => p.successful), tone: "positive" },
                { key: "failed", label: "Failed", values: stats.series.map((p) => p.failed), tone: "danger" },
              ]}
              integer
            />
          )}
        </Card>

        <Card className="flex flex-col gap-4 p-4 sm:p-6">
          <div className="flex items-baseline justify-between gap-2">
            <h2 className="text-lg font-bold">Failures by reason</h2>
            <span className="text-sm text-muted">{formatNumber(reasonsTotal)} total</span>
          </div>
          {stats.failures_by_reason.length === 0 ? (
            <p className="text-sm text-muted">No failures in this range.</p>
          ) : (
            <BarList
              label="Failures by reason"
              tone="danger"
              showShare={false}
              items={stats.failures_by_reason.map((r) => ({ key: r.code, label: r.label, value: r.count }))}
            />
          )}
        </Card>
      </div>

      <Card className="flex flex-col gap-4 p-4 sm:p-6">
        <h2 className="text-lg font-bold">Automation performance</h2>
        {stats.per_automation.length === 0 ? (
          <p className="text-sm text-muted">No automation has run in this range.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-3xl border-collapse text-left">
              <caption className="sr-only">Executions, success rate, failures and timing for each automation</caption>
              <thead>
                <tr className="text-xs font-bold uppercase tracking-wider text-faint">
                  <th scope="col" className="pb-2 pr-4">Automation</th>
                  <th scope="col" className="pb-2 pr-4 text-right">Executions</th>
                  <th scope="col" className="pb-2 pr-4">Success rate</th>
                  <th scope="col" className="pb-2 pr-4 text-right">Failed</th>
                  <th scope="col" className="pb-2 pr-4 text-right">Avg. time</th>
                  <th scope="col" className="pb-2">Last run</th>
                </tr>
              </thead>
              <tbody>
                {stats.per_automation.map((row) => (
                  <tr key={row.automation_id} className="border-t border-line">
                    <th scope="row" className="py-3 pr-4 text-left">
                      <Link
                        to={`/automations/${row.automation_id}`}
                        className="text-sm font-bold text-ink underline-offset-4 hover:text-accent-ink hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                      >
                        {row.name}
                      </Link>
                    </th>
                    <td className="py-3 pr-4 text-right text-sm tabular-nums">{formatNumber(row.executions)}</td>
                    <td className="py-3 pr-4">
                      <div className="flex items-center gap-2">
                        <meter
                          className="h-2 w-24"
                          min={0}
                          max={100}
                          value={ratePercent(row.success_rate)}
                          aria-label={`Success rate for ${row.name}`}
                        />
                        <span className="text-sm tabular-nums">{formatRate(row.success_rate)}</span>
                      </div>
                    </td>
                    <td className="py-3 pr-4 text-right text-sm tabular-nums text-negative">{formatNumber(row.failed)}</td>
                    <td className="py-3 pr-4 text-right text-sm tabular-nums">{formatDurationMs(row.avg_duration_ms)}</td>
                    <td className="py-3 text-sm text-muted">{row.last_run_at === null ? "—" : formatInstant(row.last_run_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function Analytics() {
  const titleRef = useRef<HTMLHeadingElement>(null);
  const [range, setRange] = useState(defaultRange);
  const invalid = range.from !== "" && range.to !== "" && range.from > range.to;

  const loader = useCallback(() => getStats({ from: range.from, to: range.to }), [range.from, range.to]);
  const state = useAsync(loader, [range.from, range.to]);

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/automations"
        className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <ArrowLeftIcon className="size-4" />
        All automations
      </Link>
      <PageHeading
        ref={titleRef}
        eyebrow="Automations"
        title="Automation analytics"
        description="Track how your automations perform and where they fail."
      />

      <Card className="grid gap-4 p-4 sm:grid-cols-2 sm:p-6 lg:max-w-xl">
        <TextField
          label="From"
          type="date"
          value={range.from}
          max={range.to}
          onChange={(event) => setRange((r) => ({ ...r, from: event.target.value }))}
        />
        <TextField
          label="To"
          type="date"
          value={range.to}
          min={range.from}
          {...(invalid ? { error: "The end date is before the start date." } : {})}
          onChange={(event) => setRange((r) => ({ ...r, to: event.target.value }))}
        />
      </Card>

      {state.status === "loading" ? <Loading /> : null}
      {state.status === "error" ? (
        <LoadError error={state.error} title="Couldn't load the analytics" onRetry={state.reload} />
      ) : null}
      {state.status === "success" && state.data.executions === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<ActivityIcon className="size-6" />}
            title="No executions in this range"
            description="Once a published automation runs, its results show up here. Try a wider date range, or publish an automation to get started."
            action={
              <Button variant="secondary" onClick={() => setRange(defaultRange())}>
                Reset to the last 30 days
              </Button>
            }
          />
        </Card>
      ) : null}
      {state.status === "success" && state.data.executions > 0 ? <Populated stats={state.data} /> : null}
    </div>
  );
}

export function AutomationAnalyticsPage() {
  return (
    <Announcer>
      <Analytics />
    </Announcer>
  );
}
