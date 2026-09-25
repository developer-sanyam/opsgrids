import { ChartCard, DonutChart, EmptyState, ErrorState, LineChart, ShieldIcon, WalletIcon } from "@opsgrids/ui";
import type { ChartTone, DonutSlice } from "@opsgrids/ui";

import type { MetricsSeriesOut, OrgStateOut, SubscriptionOut } from "@/api/types";
import type { AsyncState } from "@/lib/useAsync";
import { formatInt, monthLabel } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { lifecycleLabel, productLabel } from "@/lib/labels";

const RESTRICTED = (what: string) => (
  <EmptyState
    icon={<ShieldIcon className="size-6" />}
    title="Restricted"
    description={`${what} needs the owner or billing role.`}
  />
);

/* ------------------------------ account growth ----------------------------- */

interface AccountGrowthCardProps {
  series: AsyncState<MetricsSeriesOut | null>;
  canSee: boolean;
  /** The page header's period control drives this — the only range every series-backed surface
   *  on this dashboard can honour uniformly, so it lives once, not per card. */
  months: number;
}

/**
 * New organizations per month, off `GET /admin/metrics/series?months=`. A month before console's
 * own tracking window began (`value: null`) plots as zero — there is nothing to distinguish it
 * from "zero happened" on a growth line, unlike a KPI delta, where the difference matters.
 */
export function AccountGrowthCard({ series, canSee, months }: AccountGrowthCardProps) {
  const data = series.status === "success" ? series.data : null;
  const points = data?.new_organizations ?? [];

  return (
    <ChartCard
      title="Account Growth"
      description={canSee ? `New accounts, last ${months} months` : undefined}
      loading={canSee && series.status === "loading"}
      skeleton="area"
      error={canSee && series.status === "error" ? <ErrorState message={series.error.message} /> : undefined}
      empty={!canSee ? RESTRICTED("Account growth") : undefined}
    >
      <LineChart
        label="New accounts per month"
        categories={points.map((p) => monthLabel(p.period))}
        series={[{ key: "new", label: "New Accounts", values: points.map((p) => p.value ?? 0) }]}
        area
        // A count of accounts: whole-number gridlines only, and a soft curve rather than a sharp
        // spike between two small monthly counts.
        integer
        smooth
        formatValue={formatInt}
      />
    </ChartCard>
  );
}

/* ------------------------------ revenue by plan ----------------------------- */

interface RevenueByPlanCardProps {
  subscriptions: AsyncState<SubscriptionOut[] | null>;
  canSee: boolean;
}

const PAYING_STATES = new Set(["active", "past_due"]);

/**
 * MRR of every paying (active or past-due) subscription, grouped by product. Today every
 * subscription on the platform is a trial (the owner brief is explicit about this), so this is
 * legitimately empty — a well-designed empty state, never a fake donut with invented numbers.
 */
export function RevenueByPlanCard({ subscriptions, canSee }: RevenueByPlanCardProps) {
  const rows = subscriptions.status === "success" ? (subscriptions.data ?? []) : [];
  const paying = rows.filter((s) => PAYING_STATES.has(s.state));

  const byProduct = new Map<string, number>();
  for (const sub of paying) {
    const key = sub.product_name ?? productLabel(sub.product_key);
    byProduct.set(key, (byProduct.get(key) ?? 0) + sub.amount_minor);
  }
  const slices: DonutSlice[] = [...byProduct.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([label, value]) => ({ key: label, label, value }));
  const currency = paying[0]?.currency ?? "INR";
  const total = slices.reduce((sum, s) => sum + s.value, 0);

  const empty = !canSee
    ? RESTRICTED("Revenue by plan")
    : subscriptions.status === "success" && paying.length === 0 ? (
        <EmptyState
          icon={<WalletIcon className="size-6" />}
          title="No paying subscriptions yet"
          description="Every subscription on the platform is still in trial. This chart fills in as accounts convert to a paid plan."
        />
      ) : undefined;

  return (
    <ChartCard
      title="Revenue by Plan"
      description={canSee ? "MRR of paying subscriptions, by product" : undefined}
      loading={canSee && subscriptions.status === "loading"}
      skeleton="donut"
      error={canSee && subscriptions.status === "error" ? <ErrorState message={subscriptions.error.message} /> : undefined}
      empty={empty}
    >
      <DonutChart
        label="Revenue by plan"
        slices={slices}
        // The amount is still real — in the row's `title` and the accessible data table — just
        // not a visible column: the card sits beside two others at 1280px, and a rupee figure
        // beside a product name left no room for the name to read whole.
        formatValue={(v) => formatMoney(v, currency, { compact: true })}
        centerValue={formatMoney(total, currency, { compact: true })}
        size="sm"
        showValue={false}
      />
    </ChartCard>
  );
}

/* ---------------------------- accounts by status ---------------------------- */

const LIFECYCLE_TONE: Record<string, ChartTone> = {
  active: "positive",
  trial: "warning",
  suspended: "danger",
  churned: "info",
  none: "cyan",
};

interface AccountsByStatusCardProps {
  orgs: AsyncState<OrgStateOut[]>;
}

/** Every account's lifecycle bucket — the same rule `services/console/app/selectors.py` computes
 *  per row, never re-derived from anything else. */
export function AccountsByStatusCard({ orgs }: AccountsByStatusCardProps) {
  const rows = orgs.status === "success" ? orgs.data : [];
  const counts = new Map<string, number>();
  for (const org of rows) {
    counts.set(org.lifecycle, (counts.get(org.lifecycle) ?? 0) + 1);
  }
  const slices: DonutSlice[] = [...counts.entries()]
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([lifecycle, count]) => ({
      key: lifecycle,
      label: lifecycleLabel(lifecycle),
      value: count,
      tone: LIFECYCLE_TONE[lifecycle] ?? "accent",
    }));

  return (
    <ChartCard
      title="Accounts by Status"
      description="Lifecycle mix across every organization"
      loading={orgs.status === "loading"}
      skeleton="donut"
      error={orgs.status === "error" ? <ErrorState message={orgs.error.message} /> : undefined}
      empty={
        orgs.status === "success" && rows.length === 0 ? (
          <EmptyState title="No accounts yet" description="Accounts appear here as organizations sign up." />
        ) : undefined
      }
    >
      <DonutChart
        label="Accounts by status"
        slices={slices}
        formatValue={formatInt}
        centerValue={formatInt(rows.length)}
        size="sm"
        // Stacked, not beside the ring: "Suspended 1 10%" needs more width than a third of the
        // dashboard's row leaves next to even a small ring.
        layout="stacked"
      />
    </ChartCard>
  );
}
