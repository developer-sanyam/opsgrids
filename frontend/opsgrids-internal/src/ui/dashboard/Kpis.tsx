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

/**
 * Tailwind's `lg` — where the row's own default-sized cards get real width to work with (a full
 * sidebar rather than the 72px rail, per the grid comment below `GRID`). `@opsgrids/ui`'s own
 * `TABLET_QUERY` (768px) is one screen too early for *this* row: a `default`-sized card in the
 * still four-across grid between 768 and 1024px clips its own figure, which is why every tile
 * here — including the two that only ever render wide — uses this instead.
 */
const DESKTOP_QUERY = "(min-width: 1024px)";

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
  /** "Accounts", "Active" — mock-1's phone and tablet row, where "Total Accounts" would wrap or
   *  clip four-across. Falls back to `label` for a KPI that only ever renders wide. */
  shortLabel?: string;
  value: string;
  icon: ReactNode;
  tone: "accent" | "positive" | "info" | "cyan" | "warning" | "danger";
  trend?: MetricTrend;
  /** Only the first four show below 1024px (brief §2, mock-1's four-across phone *and* tablet
   *  row) — Users and Revenue need more width than a `default`-sized card gets there. */
  wideOnly?: boolean;
}

/**
 * One KPI, at the size the brief asks for — `default` (icon beside the figures) from 1024px,
 * `tile` (icon above them, four across) below it. Picked once from `useMediaQuery`, never both
 * rendered and one hidden with CSS: that would put every KPI's label in the document twice — for
 * a test, or a screen reader in a browser slow to apply the stylesheet, to trip over.
 */
function KpiTile({ kpi }: { kpi: Kpi }) {
  const wide = useMediaQuery(DESKTOP_QUERY, true);
  if (kpi.wideOnly && !wide) return null;
  return (
    <MetricCard
      label={wide ? kpi.label : (kpi.shortLabel ?? kpi.label)}
      value={kpi.value}
      icon={kpi.icon}
      tone={kpi.tone}
      size={wide ? "default" : "tile"}
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

/** Monthly Revenue: the sixth tile, hidden below 1024px like Total Users — plus its own
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
  const wide = useMediaQuery(DESKTOP_QUERY, true);
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
      // Compact ("₹13.2L"), not the full grouped figure ("₹13,24,200") — the mockup's own
      // "₹12,48,320" only fits because its sidebar is narrower than this console's; the exact
      // figure is still one hover away wherever a caller needs it (Money on the account table,
      // the Overview API response itself).
      value={formatMoney(overview.data?.mrr_minor ?? 0, overview.data?.currency ?? "INR", { compact: true })}
      icon={<WalletIcon className="size-5" />}
      tone="accent"
      {...(revenueDelta !== null ? { trend: { value: revenueDelta, period: "vs last month" } } : {})}
    />
  );
}

// Four across even on a phone (mock-1's Accounts/Active/Trial/Suspended row), a tighter gap below
// 768px where the tile itself is smaller. 6-up needs real width per card to keep every label on
// one line ("Suspended Accounts") — more than 1280px with the sidebar leaves. 3×2 from `lg`
// (1024px, where the sidebar itself appears) through `xl` (1280px), 6-up only once there is room
// for it again at `2xl` (1536px).
const GRID = "grid grid-cols-4 gap-2 md:gap-4 lg:grid-cols-3 2xl:grid-cols-6";

export function KpiRow({ orgs, overview, series, canSeeRevenue }: KpiRowProps) {
  if (orgs.status === "loading") {
    return (
      <section aria-label="Key metrics" className={GRID}>
        {Array.from({ length: 4 }, (_, i) => (
          <CardSkeleton key={i} lines={1} label="Loading key metrics" />
        ))}
        {Array.from({ length: 2 }, (_, i) => (
          <div key={`wide-${i}`} className="hidden lg:block">
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
      shortLabel: "Accounts",
      value: formatInt(total),
      icon: <BuildingIcon className="size-5" />,
      tone: "info",
      ...(totalDelta !== null ? { trend: { value: totalDelta, period: "vs last month" } } : {}),
    },
    {
      key: "active",
      label: "Active Accounts",
      shortLabel: "Active",
      value: formatInt(active),
      icon: <CheckCircleIcon className="size-5" />,
      tone: "positive",
    },
    {
      key: "trial",
      label: "Trial Accounts",
      shortLabel: "Trial",
      value: formatInt(trial),
      icon: <ClockIcon className="size-5" />,
      tone: "warning",
    },
    {
      key: "suspended",
      label: "Suspended Accounts",
      shortLabel: "Suspended",
      value: formatInt(suspended),
      icon: <PauseCircleIcon className="size-5" />,
      tone: "danger",
    },
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
