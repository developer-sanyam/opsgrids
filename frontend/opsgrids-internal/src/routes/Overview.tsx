import { useMemo } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import {
  BuildingIcon,
  GaugeIcon,
  LayersIcon,
  LockIcon,
  RepeatIcon,
  WalletIcon,
} from "@opsgrids/ui";

import { getOverview, listOrganizations } from "@/api/client";
import type { OrganizationRow, OverviewMetrics } from "@/api/types";
import { daysUntil, formatPercent, relativeDays } from "@/lib/format";
import {
  AccountStatusPill,
  BreakdownBar,
  Card,
  DeltaPill,
  EnvBadge,
  KpiCard,
  LoadFailure,
  Money,
  MrrTrend,
  PageHeader,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { KpiTone } from "@/ui";

interface KpiConfig {
  key: string;
  label: string;
  value: string;
  deltaPct?: number;
  series?: number[];
  icon: ReactNode;
  tone: KpiTone;
  caption: string;
  goodWhen?: "up" | "down";
}

function buildKpis(m: OverviewMetrics): KpiConfig[] {
  return [
    {
      key: "mrr",
      label: "Monthly recurring revenue",
      value: new Intl.NumberFormat("en-IN", {
        style: "currency",
        currency: m.currency,
        notation: "compact",
        maximumFractionDigits: 1,
      }).format(m.mrr_minor / 100),
      deltaPct: m.mrr_delta_pct,
      series: m.mrr_sparkline,
      icon: <GaugeIcon className="size-5" />,
      tone: "accent",
      caption: "vs last month",
    },
    {
      key: "active",
      label: "Active accounts",
      value: String(m.active_accounts),
      deltaPct: m.active_delta_pct,
      series: m.active_sparkline,
      icon: <BuildingIcon className="size-5" />,
      tone: "positive",
      caption: "customers billing",
    },
    {
      key: "trials",
      label: "Trials in progress",
      value: String(m.trials_in_progress),
      deltaPct: m.trials_delta_pct,
      series: m.trials_sparkline,
      icon: <LayersIcon className="size-5" />,
      tone: "info",
      caption: "evaluating now",
    },
    {
      key: "conversion",
      label: "Trial → paid conversion",
      value: formatPercent(m.trial_conversion_pct),
      deltaPct: m.conversion_delta_pct,
      series: m.conversion_sparkline,
      icon: <RepeatIcon className="size-5" />,
      tone: "cyan",
      caption: "last 90 days",
    },
    {
      key: "churn",
      label: "Logo churn",
      value: formatPercent(m.churn_pct),
      deltaPct: m.churn_delta_pct,
      series: m.churn_sparkline,
      icon: <GaugeIcon className="size-5" />,
      tone: "warning",
      caption: "monthly, lower is better",
      goodWhen: "down",
    },
    {
      // A count with no history behind it: no delta and no sparkline, rather than an invented 0%.
      key: "locked",
      label: "Locked accounts",
      value: String(m.locked_accounts),
      icon: <LockIcon className="size-5" />,
      tone: "danger",
      caption: `${m.suspended_accounts} suspended`,
    },
  ];
}

interface Attention {
  org: OrganizationRow;
  reason: string;
  weight: number;
}

function attentionRows(orgs: OrganizationRow[]): Attention[] {
  const rows: Attention[] = [];
  for (const org of orgs) {
    if (org.status === "suspended") {
      rows.push({ org, reason: org.locked ? "Suspended — access locked" : "Suspended", weight: 3 });
    } else if (org.status === "past_due") {
      rows.push({ org, reason: "Payment past due — in dunning", weight: 2 });
    } else if (org.status === "trial") {
      const left = daysUntil(org.trial_ends_at);
      if (left !== null && left <= 12) {
        rows.push({ org, reason: `Trial ends ${relativeDays(org.trial_ends_at)}`, weight: 1 });
      }
    }
  }
  return rows.sort((a, b) => b.weight - a.weight);
}

/** Six KPI cards' worth of placeholder, so the row does not move when the figures arrive. */
function KpiSkeletons() {
  return (
    <>
      {Array.from({ length: 6 }).map((_, i) => (
        <Card key={i} className="flex flex-col gap-4 p-5" aria-hidden>
          <div className="flex justify-between">
            <Skeleton className="size-10 rounded-tile" />
            <Skeleton className="h-8 w-20" />
          </div>
          <Skeleton className="h-4 w-28" />
          <Skeleton className="h-9 w-24 rounded-tile" />
        </Card>
      ))}
    </>
  );
}

export function OverviewPage() {
  const overview = useAsync(getOverview, []);
  const orgs = useAsync(() => listOrganizations(), []);

  const attention = useMemo(
    () => (orgs.status === "success" ? attentionRows(orgs.data.items) : []),
    [orgs],
  );

  const metrics = overview.status === "success" ? overview.data : null;

  return (
    <div className="rise flex flex-col gap-5">
      <PageHeader
        eyebrow="Cross-tenant · all organizations"
        title="Overview"
        description="Revenue, lifecycle and the accounts that need an operator's attention — projected from the commerce event stream."
        actions={
          <>
            <EnvBadge />
            <span className="inline-flex h-9 items-center rounded-full bg-surface px-3 text-sm font-semibold text-ink-soft shadow-card">
              {metrics ? metrics.period_label : "—"}
            </span>
          </>
        }
      />

      {overview.status === "error" ? (
        <Card className="p-5">
          <LoadFailure
            error={overview.error}
            title="Couldn't load the platform metrics"
            onRetry={overview.reload}
          />
        </Card>
      ) : (
        <>
          {/* KPI row */}
          <section
            aria-label="Key metrics"
            aria-busy={overview.status === "loading"}
            className="cascade grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3"
          >
            {metrics ? (
              buildKpis(metrics).map((kpi, i) => (
                <div key={kpi.key} style={{ "--i": i } as CSSProperties}>
                  <KpiCard
                    label={kpi.label}
                    value={kpi.value}
                    deltaPct={kpi.deltaPct}
                    series={kpi.series}
                    icon={kpi.icon}
                    tone={kpi.tone}
                    caption={kpi.caption}
                    goodWhen={kpi.goodWhen}
                  />
                </div>
              ))
            ) : (
              <KpiSkeletons />
            )}
          </section>

          {/* MRR trend + revenue this period */}
          <section aria-label="Revenue" className="grid grid-cols-1 gap-5 xl:grid-cols-3">
            <Card className="flex flex-col gap-5 p-6 xl:col-span-2">
              <SectionHeader
                title="MRR over time"
                subtitle="Running sum of active subscriptions × pinned price"
              />
              {metrics ? (
                <>
                  <div className="flex flex-wrap items-center gap-3">
                    <Money
                      minor={metrics.mrr_minor}
                      currency={metrics.currency}
                      className="display text-4xl font-extrabold leading-none"
                    />
                    <DeltaPill value={metrics.mrr_delta_pct} />
                    <span className="text-sm text-muted">trailing 12 months</span>
                  </div>
                  <MrrTrend series={metrics.mrr_series} currency={metrics.currency} />
                </>
              ) : (
                <Skeleton className="h-56 w-full rounded-tile" />
              )}
            </Card>

            <Card tone="dark" className="flex flex-col justify-between gap-6 p-6">
              <div className="flex items-start justify-between">
                <span className="grid size-10 place-items-center rounded-tile bg-white/10 text-white">
                  <WalletIcon className="size-5" />
                </span>
                {metrics ? <DeltaPill value={metrics.revenue_delta_pct} /> : null}
              </div>
              <div className="flex flex-col gap-2">
                <span className="text-sm font-medium text-white/70">Revenue this period</span>
                {metrics ? (
                  <>
                    <Money
                      minor={metrics.revenue_period_minor}
                      currency={metrics.currency}
                      className="display text-4xl font-extrabold leading-none text-white"
                    />
                    <span className="text-sm text-white/60">
                      Finalized invoices · {metrics.period_label}
                    </span>
                  </>
                ) : (
                  <Skeleton className="h-10 w-32 rounded-tile bg-white/10" />
                )}
              </div>
            </Card>
          </section>

          {/* Funnels */}
          <section aria-label="Lifecycle" className="grid grid-cols-1 gap-5 xl:grid-cols-2">
            <Card className="flex flex-col gap-5 p-6">
              <SectionHeader title="Trial funnel" subtitle="Trailing 90 days" />
              {metrics ? (
                <BreakdownBar segments={metrics.trial_funnel} unit="accounts" />
              ) : (
                <Skeleton className="h-40 w-full rounded-tile" />
              )}
            </Card>
            <Card className="flex flex-col gap-5 p-6">
              <SectionHeader title="Account lifecycle" subtitle="Where the customer base sits today" />
              {metrics ? (
                <BreakdownBar segments={metrics.churn_breakdown} unit="accounts" />
              ) : (
                <Skeleton className="h-40 w-full rounded-tile" />
              )}
            </Card>
          </section>
        </>
      )}

      {/* Needs attention */}
      <Card className="flex flex-col gap-4 p-6">
        <SectionHeader
          title="Needs attention"
          subtitle="Suspensions, dunning and trials about to lapse"
          action={
            <Link
              to="/accounts"
              className="text-sm font-semibold text-accent transition-colors hover:text-accent-strong"
            >
              All accounts
            </Link>
          }
        />
        {orgs.status === "loading" ? (
          <div className="flex flex-col gap-3" aria-busy>
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full rounded-tile" />
            ))}
          </div>
        ) : orgs.status === "error" ? (
          <LoadFailure error={orgs.error} title="Couldn't load accounts" onRetry={orgs.reload} />
        ) : attention.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted">
            Nothing needs attention — every account is active and paid.
          </p>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {attention.map(({ org, reason }) => (
              <li key={org.organization_id}>
                <Link
                  to={`/accounts/${org.organization_id}`}
                  className="flex flex-wrap items-center gap-3 py-3 transition-colors hover:bg-ground/60"
                >
                  <AccountStatusPill status={org.status} />
                  <span className="font-bold text-ink">{org.name}</span>
                  <span className="text-sm text-muted">{reason}</span>
                  <span className="ml-auto flex items-center gap-4">
                    {org.mrr_minor > 0 ? (
                      <Money minor={org.mrr_minor} currency={org.currency} className="text-sm font-semibold" />
                    ) : (
                      <span className="text-sm text-faint">no MRR</span>
                    )}
                    {org.locked ? (
                      <span className="inline-flex items-center gap-1 text-xs font-semibold text-danger">
                        <LockIcon className="size-3.5" /> Locked
                      </span>
                    ) : null}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
