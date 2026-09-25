import type { ReactNode } from "react";

import {
  BuildingIcon,
  Card,
  CardSkeleton,
  CheckCircleIcon,
  ClockIcon,
  ErrorState,
  MetricCard,
  PauseCircleIcon,
  ShieldIcon,
  TABLET_QUERY,
  useMediaQuery,
  UsersIcon,
  WalletIcon,
} from "@opsgrids/ui";
import type { MetricTrend } from "@opsgrids/ui";

import type { MetricsOverviewOut, MetricsSeriesOut, OrgStateOut } from "@/api/types";
import type { AsyncState } from "@/lib/useAsync";
import { growthDelta, seriesDelta } from "@/lib/deltas";
import { formatInt } from "@/lib/format";
import { formatMoney } from "@/lib/money";

interface KpiRowProps {
  orgs: AsyncState<OrgStateOut[]>;
  /**
   * `null` (once `status` is `"success"`) means "not fetched at all" — the operator lacks
   * `owner`/`billing_ops`, so the console never called the billing-gated route in the first place.
   * `canSeeRevenue` is what actually decides the tile shown; these two are never fetched unless it
   * is true, so a support operator's dashboard makes neither of these two calls.
   */
  overview: AsyncState<MetricsOverviewOut | null>;
  series: AsyncState<MetricsSeriesOut | null>;
  canSeeRevenue: boolean;
}

interface Kpi {
  key: string;
  label: string;
  value: string;
  icon: ReactNode;
  tone: "accent" | "positive" | "info" | "cyan" | "warning" | "danger";
  trend?: MetricTrend;
  /** Only the first four show on a phone (brief §2, mock-1's 2×2 grid) — Users and Revenue need
   *  more width than a two-up phone row has to spare. */
  wideOnly?: boolean;
}

/**
 * One KPI, at the size the brief asks for — `default` (icon beside the figures) from 768px,
 * `compact` (stacked) below it. Picked once from `useMediaQuery`, never both rendered and one
 * hidden with CSS: that would put every KPI's label in the document twice — for a test, or a
 * screen reader in a browser slow to apply the stylesheet, to trip over.
 */
function KpiTile({ kpi }: { kpi: Kpi }) {
  const wide = useMediaQuery(TABLET_QUERY, true);
  if (kpi.wideOnly && !wide) return null;
  return (
    <MetricCard
      label={kpi.label}
      value={kpi.value}
      icon={kpi.icon}
      tone={kpi.tone}
      size={wide ? "default" : "compact"}
      {...(kpi.trend ? { trend: kpi.trend } : {})}
    />
  );
}

/** Every account's lifecycle bucket, counted once — the same three buckets the KPI row needs. */
function lifecycleCounts(orgs: OrgStateOut[]) {
  let active = 0;
  let trial = 0;
  let suspended = 0;
  for (const org of orgs) {
    if (org.lifecycle === "active") active += 1;
    else if (org.lifecycle === "trial") trial += 1;
    else if (org.lifecycle === "suspended") suspended += 1;
  }
  return { active, trial, suspended };
}

/** The sum of every organization's known member count. `null` rows (account-service could not be
 *  reached for them) are skipped rather than counted as zero — this is a floor on the real total,
 *  not a claim of exactness, which is why it is never marked as "sample" data: every number in it
 *  is real, just possibly incomplete. */
function totalUsers(orgs: OrgStateOut[]): number {
  return orgs.reduce((sum, org) => sum + (org.user_count ?? 0), 0);
}

/** The one KPI tile that needs `owner`/`billing_ops` — an honest permission-denied state rather
 *  than a hidden card, so the six-tile grid never silently shrinks to five. */
function RestrictedRevenueCard() {
  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
      <span className="grid size-10 place-items-center rounded-tile bg-sunken text-ink-soft">
        <ShieldIcon className="size-5" />
      </span>
      <div className="flex flex-col gap-1">
        <span className="text-sm font-medium text-muted">Monthly Revenue</span>
        <span className="text-xl font-bold leading-tight text-ink-soft">Restricted</span>
        <span className="text-xs text-faint">Needs the owner or billing role</span>
      </div>
    </Card>
  );
}

/** Monthly Revenue: the sixth tile, hidden below 768px like Total Users — plus its own
 *  loading/error/restricted branches, which is why it isn't just another `Kpi` config entry. */
function RevenueTile({
  overview,
  revenueDelta,
  canSeeRevenue,
}: {
  overview: AsyncState<MetricsOverviewOut | null>;
  revenueDelta: number | null;
  canSeeRevenue: boolean;
}) {
  const wide = useMediaQuery(TABLET_QUERY, true);
  if (!wide) return null;
  if (!canSeeRevenue) return <RestrictedRevenueCard />;
  if (overview.status === "loading") return <CardSkeleton lines={1} label="Loading monthly revenue" />;
  if (overview.status === "error") {
    return (
      <Card className="p-4 sm:p-6">
        <ErrorState message={overview.error.message} />
      </Card>
    );
  }
  return (
    <MetricCard
      label="Monthly Revenue"
      value={formatMoney(overview.data?.mrr_minor ?? 0, overview.data?.currency ?? "INR")}
      icon={<WalletIcon className="size-5" />}
      tone="accent"
      {...(revenueDelta !== null ? { trend: { value: revenueDelta, period: "vs last month" } } : {})}
    />
  );
}

const GRID = "grid grid-cols-2 gap-4 md:grid-cols-4 xl:grid-cols-6";

export function KpiRow({ orgs, overview, series, canSeeRevenue }: KpiRowProps) {
  if (orgs.status === "loading") {
    return (
      <section aria-label="Key metrics" className={GRID}>
        {Array.from({ length: 4 }, (_, i) => (
          <CardSkeleton key={i} lines={1} label="Loading key metrics" />
        ))}
        {Array.from({ length: 2 }, (_, i) => (
          <div key={`wide-${i}`} className="hidden md:block">
            <CardSkeleton lines={1} label="Loading key metrics" />
          </div>
        ))}
      </section>
    );
  }

  if (orgs.status === "error") {
    return (
      <section aria-label="Key metrics">
        <Card className="p-6">
          <ErrorState message={orgs.error.message} />
        </Card>
      </section>
    );
  }

  const { active, trial, suspended } = lifecycleCounts(orgs.data);
  const userTotal = totalUsers(orgs.data);
  const total = orgs.data.length;

  const seriesData = series.status === "success" ? series.data : null;
  const totalDelta = seriesData ? growthDelta(seriesData.new_organizations, total) : null;
  const revenueDelta = seriesData ? seriesDelta(seriesData.mrr_minor) : null;

  const kpis: Kpi[] = [
    {
      key: "total",
      label: "Total Accounts",
      value: formatInt(total),
      icon: <BuildingIcon className="size-5" />,
      tone: "info",
      ...(totalDelta !== null ? { trend: { value: totalDelta, period: "vs last month" } } : {}),
    },
    { key: "active", label: "Active Accounts", value: formatInt(active), icon: <CheckCircleIcon className="size-5" />, tone: "positive" },
    { key: "trial", label: "Trial Accounts", value: formatInt(trial), icon: <ClockIcon className="size-5" />, tone: "warning" },
    { key: "suspended", label: "Suspended Accounts", value: formatInt(suspended), icon: <PauseCircleIcon className="size-5" />, tone: "danger" },
    { key: "users", label: "Total Users", value: formatInt(userTotal), icon: <UsersIcon className="size-5" />, tone: "cyan", wideOnly: true },
  ];

  return (
    <section aria-label="Key metrics" className={GRID}>
      {kpis.map((kpi) => (
        <KpiTile key={kpi.key} kpi={kpi} />
      ))}
      <RevenueTile overview={overview} revenueDelta={revenueDelta} canSeeRevenue={canSeeRevenue} />
    </section>
  );
}
