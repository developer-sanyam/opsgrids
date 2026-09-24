import { useMemo } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";

import { getOverview, listOrganizations } from "@/api/client";
import type { BreakdownSegment, MetricsOverviewOut, OrgStateOut } from "@/api/types";
import { daysUntil, formatPercent, relativeDays } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import {
  BreakdownBar,
  Card,
  EnvBadge,
  ErrorState,
  KpiCard,
  Money,
  OrgIdentity,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { KpiTone } from "@/ui";
import { BuildingsIcon, GaugeIcon, LayersIcon, LockIcon, RepeatIcon } from "@/ui/icons";

interface KpiConfig {
  key: string;
  label: string;
  value: string;
  icon: ReactNode;
  tone: KpiTone;
  caption: string;
}

/**
 * `GET /admin/metrics/overview` carries no historical series and no period-over-period delta — see
 * `types.ts`'s `MetricsOverviewOut`. Every tile below is a plain current figure; none fabricates a
 * trend the API doesn't send.
 */
function buildKpis(m: MetricsOverviewOut, lockedAccounts: number): KpiConfig[] {
  return [
    {
      key: "mrr",
      label: "Monthly recurring revenue",
      value: new Intl.NumberFormat("en-IN", {
        style: "currency",
        currency: m.currency ?? "INR",
        notation: "compact",
        maximumFractionDigits: 1,
      }).format(m.mrr_minor / 100),
      icon: <GaugeIcon className="size-5" />,
      tone: "accent",
      caption: "current",
    },
    {
      key: "active",
      label: "Active subscriptions",
      value: String(m.active_subscriptions),
      icon: <BuildingsIcon className="size-5" />,
      tone: "active",
      caption: `${m.organization_count} organizations total`,
    },
    {
      key: "trials",
      label: "Trials started",
      value: String(m.trials_started),
      icon: <LayersIcon className="size-5" />,
      tone: "trial",
      caption: `${m.trial_conversions} converted`,
    },
    {
      key: "conversion",
      label: "Trial → paid conversion",
      // The service sends rates as fractions (0.4321), not percentages.
      value: formatPercent(m.trial_conversion_rate * 100),
      icon: <RepeatIcon className="size-5" />,
      tone: "comp",
      caption: "of trials started",
    },
    {
      key: "churn",
      label: "Churn",
      value: formatPercent(m.churn_rate * 100),
      icon: <GaugeIcon className="size-5" />,
      tone: "pastdue",
      caption: `${m.churned} churned`,
    },
    {
      key: "locked",
      label: "Locked accounts",
      value: String(lockedAccounts),
      icon: <LockIcon className="size-5" />,
      tone: "suspended",
      caption: `${m.organizations_by_lifecycle.suspended} suspended`,
    },
  ];
}

function lifecycleSegments(m: MetricsOverviewOut): BreakdownSegment[] {
  const c = m.organizations_by_lifecycle;
  return [
    { key: "active", label: "Active", count: c.active, tone: "active" },
    { key: "trial", label: "Trial", count: c.trial, tone: "trial" },
    { key: "suspended", label: "Suspended", count: c.suspended, tone: "suspended" },
    { key: "churned", label: "Churned", count: c.churned, tone: "cancelled" },
    { key: "none", label: "No subscription", count: c.none, tone: "accent" },
  ];
}

interface Attention {
  org: OrgStateOut;
  reason: string;
  weight: number;
}

/** `OrgStateOut` carries no lifecycle status — only `locked` and `trial_ends_at` are real fields
 *  to flag from. There is no way to surface "past due" from this endpoint (see `types.ts`). */
function attentionRows(orgs: OrgStateOut[]): Attention[] {
  const rows: Attention[] = [];
  for (const org of orgs) {
    if (org.locked) {
      rows.push({ org, reason: "Locked — access suspended", weight: 2 });
      continue;
    }
    const left = daysUntil(org.trial_ends_at);
    if (left !== null && left <= 12) {
      rows.push({ org, reason: `Trial ends ${relativeDays(org.trial_ends_at)}`, weight: 1 });
    }
  }
  return rows.sort((a, b) => b.weight - a.weight);
}

export function OverviewPage() {
  const overview = useAsync(getOverview, []);
  const orgs = useAsync(() => listOrganizations(), []);

  const attention = useMemo(
    () => (orgs.status === "success" ? attentionRows(orgs.data.organizations) : []),
    [orgs],
  );
  const lockedAccounts = useMemo(
    () => (orgs.status === "success" ? orgs.data.organizations.filter((o) => o.locked).length : 0),
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
        <EnvBadge />
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
          ? buildKpis(overview.data, lockedAccounts).map((kpi, i) => (
              <div key={kpi.key} style={{ "--i": i } as CSSProperties}>
                <KpiCard
                  label={kpi.label}
                  value={kpi.value}
                  deltaPct={null}
                  series={[]}
                  icon={kpi.icon}
                  tone={kpi.tone}
                  caption={kpi.caption}
                  hideSparkline
                />
              </div>
            ))
          : null}
      </section>

      {/* Lifecycle breakdown */}
      <Card className="flex flex-col gap-5 p-6">
        <SectionHeader
          title="Organizations by lifecycle"
          subtitle="Real counts from the platform metrics projection"
        />
        {overview.status === "success" ? (
          <BreakdownBar segments={lifecycleSegments(overview.data)} unit="organizations" />
        ) : overview.status === "error" ? (
          <ErrorState message={overview.error.message} />
        ) : (
          <Skeleton className="h-40 w-full" />
        )}
      </Card>

      {/* Needs attention */}
      <Card className="flex flex-col gap-4 p-6">
        <SectionHeader
          title="Needs attention"
          subtitle="Locked accounts and trials about to lapse"
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
            Nothing needs attention — every account is unlocked with no trial ending soon.
          </p>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {attention.map(({ org, reason }) => (
              <li key={org.organization_id}>
                <Link
                  to={`/accounts/${org.organization_id}`}
                  className="flex flex-wrap items-center gap-3 py-3 transition-colors hover:bg-ground/60"
                >
                  <OrgIdentity
                    name={org.name}
                    slug={org.slug}
                    organizationId={org.organization_id}
                    className="min-w-0 max-w-xs"
                  />
                  <span className="text-sm text-muted">{reason}</span>
                  <span className="ml-auto flex items-center gap-4">
                    {org.mrr_minor > 0 ? (
                      <Money minor={org.mrr_minor} currency={org.currency ?? "INR"} className="text-sm font-semibold" />
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
