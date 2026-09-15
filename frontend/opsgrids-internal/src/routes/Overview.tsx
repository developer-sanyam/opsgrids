import { useMemo } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";

import { getOverview, listOrganizations } from "@/api/client";
import type { OrganizationRow, OverviewMetrics } from "@/api/types";
import { daysUntil, formatPercent, relativeDays } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import {
  AccountStatusPill,
  BreakdownBar,
  Card,
  DeltaPill,
  EnvBadge,
  ErrorState,
  KpiCard,
  Money,
  MrrTrend,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { KpiTone } from "@/ui";
import {
  BuildingsIcon,
  GaugeIcon,
  LayersIcon,
  LockIcon,
  RepeatIcon,
  WalletIcon,
} from "@/ui/icons";

interface KpiConfig {
  key: string;
  label: string;
  value: string;
  deltaPct: number;
  series: number[];
  icon: ReactNode;
  tone: KpiTone;
  caption: string;
  positiveIsGood?: boolean;
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
      icon: <BuildingsIcon className="size-5" />,
      tone: "active",
      caption: "customers billing",
    },
    {
      key: "trials",
      label: "Trials in progress",
      value: String(m.trials_in_progress),
      deltaPct: m.trials_delta_pct,
      series: m.trials_sparkline,
      icon: <LayersIcon className="size-5" />,
      tone: "trial",
      caption: "evaluating now",
    },
    {
      key: "conversion",
      label: "Trial → paid conversion",
      value: formatPercent(m.trial_conversion_pct),
      deltaPct: m.conversion_delta_pct,
      series: m.conversion_sparkline,
      icon: <RepeatIcon className="size-5" />,
      tone: "comp",
      caption: "last 90 days",
    },
    {
      key: "churn",
      label: "Logo churn",
      value: formatPercent(m.churn_pct),
      deltaPct: m.churn_delta_pct,
      series: m.churn_sparkline,
      icon: <GaugeIcon className="size-5" />,
      tone: "pastdue",
      caption: "monthly, lower is better",
      positiveIsGood: false,
    },
    {
      key: "locked",
      label: "Locked accounts",
      value: String(m.locked_accounts),
      deltaPct: 0,
      series: [],
      icon: <LockIcon className="size-5" />,
      tone: "suspended",
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

export function OverviewPage() {
  const overview = useAsync(getOverview, []);
  const orgs = useAsync(() => listOrganizations(), []);

  const attention = useMemo(
    () => (orgs.status === "success" ? attentionRows(orgs.data.items) : []),
    [orgs],
  );

  return (
    <div className="rise flex flex-col gap-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <span className="text-sm font-semibold text-accent">Cross-tenant · all organizations</span>
          <h1 className="display text-3xl font-extrabold leading-tight sm:text-[2.1rem]">Overview</h1>
          <p className="max-w-2xl text-sm text-muted">
            Revenue, lifecycle and the accounts that need an operator's attention — projected from
            the commerce event stream.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <EnvBadge />
          <span className="inline-flex h-9 items-center rounded-full bg-surface px-3 text-sm font-semibold text-ink-soft shadow-card">
            {overview.status === "success" ? overview.data.period_label : "—"}
          </span>
        </div>
      </header>

      {/* KPI row */}
      <section aria-label="Key metrics" className="cascade grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {overview.status === "loading"
          ? Array.from({ length: 6 }).map((_, i) => (
              <Card key={i} className="flex flex-col gap-4 p-5" style={{ "--i": i } as CSSProperties}>
                <div className="flex justify-between">
                  <Skeleton className="size-10 rounded-tile" />
                  <Skeleton className="h-8 w-20" />
                </div>
                <Skeleton className="h-4 w-28" />
                <Skeleton className="h-9 w-24 rounded-tile" />
              </Card>
            ))
          : null}
        {overview.status === "error" ? (
          <Card className="p-5 sm:col-span-2 xl:col-span-3">
            <ErrorState message={overview.error.message} />
          </Card>
        ) : null}
        {overview.status === "success"
          ? buildKpis(overview.data).map((kpi, i) => (
              <div key={kpi.key} style={{ "--i": i } as CSSProperties}>
                <KpiCard
                  label={kpi.label}
                  value={kpi.value}
                  deltaPct={kpi.deltaPct}
                  series={kpi.series}
                  icon={kpi.icon}
                  tone={kpi.tone}
                  caption={kpi.caption}
                  positiveIsGood={kpi.positiveIsGood ?? true}
                  hideSparkline={kpi.series.length === 0}
                />
              </div>
            ))
          : null}
      </section>

      {/* MRR trend + revenue this period */}
      <section aria-label="Revenue" className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <Card className="flex flex-col gap-5 p-6 xl:col-span-2">
          <SectionHeader title="MRR over time" subtitle="Running sum of active subscriptions × pinned price" />
          {overview.status === "success" ? (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <Money
                  minor={overview.data.mrr_minor}
                  currency={overview.data.currency}
                  className="display text-[2.2rem] font-extrabold leading-none"
                />
                <DeltaPill value={overview.data.mrr_delta_pct} />
                <span className="text-sm text-muted">trailing 12 months</span>
              </div>
              <MrrTrend series={overview.data.mrr_series} currency={overview.data.currency} />
            </>
          ) : overview.status === "error" ? (
            <ErrorState message={overview.error.message} />
          ) : (
            <Skeleton className="h-56 w-full" />
          )}
        </Card>

        <Card tone="dark" className="flex flex-col justify-between gap-6 p-6">
          <div className="flex items-start justify-between">
            <span className="grid size-10 place-items-center rounded-tile bg-white/10 text-white">
              <WalletIcon className="size-5" />
            </span>
            {overview.status === "success" ? <DeltaPill value={overview.data.revenue_delta_pct} /> : null}
          </div>
          <div className="flex flex-col gap-2">
            <span className="text-sm font-medium text-white/70">Revenue this period</span>
            {overview.status === "success" ? (
              <>
                <Money
                  minor={overview.data.revenue_period_minor}
                  currency={overview.data.currency}
                  className="display text-[2.4rem] font-extrabold leading-none text-white"
                />
                <span className="text-sm text-white/60">
                  Finalized invoices · {overview.data.period_label}
                </span>
              </>
            ) : (
              <Skeleton className="h-10 w-32 bg-white/10" />
            )}
          </div>
        </Card>
      </section>

      {/* Funnels */}
      <section aria-label="Lifecycle" className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Card className="flex flex-col gap-5 p-6">
          <SectionHeader title="Trial funnel" subtitle="Trailing 90 days" />
          {overview.status === "success" ? (
            <BreakdownBar segments={overview.data.trial_funnel} unit="accounts" />
          ) : (
            <Skeleton className="h-40 w-full" />
          )}
        </Card>
        <Card className="flex flex-col gap-5 p-6">
          <SectionHeader title="Account lifecycle" subtitle="Where the customer base sits today" />
          {overview.status === "success" ? (
            <BreakdownBar segments={overview.data.churn_breakdown} unit="accounts" />
          ) : (
            <Skeleton className="h-40 w-full" />
          )}
        </Card>
      </section>

      {/* Needs attention */}
      <Card className="flex flex-col gap-4 p-6">
        <SectionHeader
          title="Needs attention"
          subtitle="Suspensions, dunning and trials about to lapse"
          action={
            <Link to="/accounts" className="text-sm font-semibold text-accent hover:text-accent-strong transition-colors">
              All accounts
            </Link>
          }
        />
        {orgs.status === "loading" ? (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : orgs.status === "error" ? (
          <ErrorState message={orgs.error.message} />
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
                      <span className="inline-flex items-center gap-1 text-xs font-semibold text-suspended">
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
