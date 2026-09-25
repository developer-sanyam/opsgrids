import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { CheckCircleIcon, PageHeader, Select } from "@opsgrids/ui";

import { getMetricsSeries, getOverview, listAudit, listOrganizations, listSubscriptions } from "@/api/client";
import type { OrgStateOut, OverrideResult, PricingOrgRef } from "@/api/types";
import { useAuth } from "@/auth/context";
import { useAsync } from "@/lib/useAsync";
import { AccountGrowthCard, AccountsByStatusCard, RevenueByPlanCard } from "@/ui/dashboard/Charts";
import { AccountsTable } from "@/ui/dashboard/AccountsTable";
import { RecentActivityCard, TrialsEndingCard } from "@/ui/dashboard/ActivityPanels";
import { KpiRow } from "@/ui/dashboard/Kpis";
import { ReactivateAction, SuspendAction } from "./AccountActions";

const MONTH_OPTIONS = [
  { value: "3", label: "Last 3 months" },
  { value: "6", label: "Last 6 months" },
  { value: "12", label: "Last 12 months" },
];

type RowAction = { kind: "suspend" | "reactivate"; org: PricingOrgRef };

function toOrgRef(org: OrgStateOut): PricingOrgRef {
  return { organization_id: org.organization_id, name: org.name ?? org.slug ?? org.organization_id };
}

/**
 * The Super Admin Dashboard (mock-1): every account on the platform, its revenue and lifecycle,
 * and the accounts that need an operator's attention. `owner` sees the whole page; `billing_ops`
 * and `support` each see a narrower one — the same `require_operator` role matrix the admin
 * gateway enforces, applied here only to decide which billing- or audit-gated route this page
 * calls at all, never as the only thing standing between a caller and the data (CLAUDE.md).
 */
export function OverviewPage() {
  const { hasRole } = useAuth();
  const navigate = useNavigate();
  const [months, setMonths] = useState(6);
  const [reloadKey, setReloadKey] = useState(0);
  const [rowAction, setRowAction] = useState<RowAction | null>(null);
  const [banner, setBanner] = useState<OverrideResult | null>(null);

  const canSeeRevenue = hasRole("owner") || hasRole("billing_ops");
  const canSeeAudit = hasRole("owner") || hasRole("support");
  const canWriteOverrides = hasRole("owner") || hasRole("billing_ops");

  const retry = useCallback(() => setReloadKey((k) => k + 1), []);

  const orgs = useAsync(() => listOrganizations().then((r) => r.organizations), [reloadKey]);
  const overview = useAsync(
    () => (canSeeRevenue ? getOverview() : Promise.resolve(null)),
    [canSeeRevenue, reloadKey],
  );
  const series = useAsync(
    () => (canSeeRevenue ? getMetricsSeries(months) : Promise.resolve(null)),
    [canSeeRevenue, months, reloadKey],
  );
  const subscriptions = useAsync(
    () => (canSeeRevenue ? listSubscriptions().then((r) => r.subscriptions) : Promise.resolve(null)),
    [canSeeRevenue, reloadKey],
  );
  const audit = useAsync(
    () => (canSeeAudit ? listAudit().then((r) => r.audit) : Promise.resolve(null)),
    [canSeeAudit, reloadKey],
  );

  const orgNameById = useMemo(() => {
    const map = new Map<string, string>();
    if (orgs.status === "success") {
      for (const org of orgs.data) map.set(org.organization_id, org.name ?? org.slug ?? org.organization_id);
    }
    return map;
  }, [orgs]);

  function openAccount(org: OrgStateOut) {
    navigate(`/accounts/${org.organization_id}`);
  }
  function openPricing(org: OrgStateOut) {
    navigate(`/accounts/${org.organization_id}#pricing`);
  }

  return (
    <div className="rise flex flex-col gap-5">
      <PageHeader
        title={
          <>
            <span className="sm:hidden">Super Admin</span>
            <span className="hidden sm:inline">Super Admin Dashboard</span>
          </>
        }
        description="Manage all accounts, monitor usage, and oversee the platform."
        descriptionClassName="hidden sm:block"
        actions={
          canSeeRevenue ? (
            <Select
              label="Period"
              hideLabel
              value={String(months)}
              onChange={(e) => setMonths(Number(e.target.value))}
              options={MONTH_OPTIONS}
              containerClassName="min-w-40"
            />
          ) : undefined
        }
      />

      {banner ? (
        <div role="status" className="flex items-start gap-3 rounded-card bg-present-soft px-4 py-3 text-sm text-present">
          <CheckCircleIcon className="mt-1 size-5 shrink-0" />
          <div className="flex flex-col">
            <span className="font-semibold">Override accepted</span>
            <span className="text-present/80">Delegated to {banner.delegated_to}-service.</span>
          </div>
          <button
            type="button"
            onClick={() => setBanner(null)}
            className="ml-auto text-xs font-semibold text-present/70 hover:text-present"
          >
            Dismiss
          </button>
        </div>
      ) : null}

      <KpiRow orgs={orgs} overview={overview} series={series} canSeeRevenue={canSeeRevenue} />

      {/* Below `xl` (mock-1's phone and tablet frames): growth chart, then the accounts list,
          then Revenue by Plan and Accounts by Status — `order` only, so a screen reader still
          reads these four regions in the same sequence regardless of width. From `xl` (the
          desktop frame) all three charts sit in one row and the table returns to full width
          below them, exactly the DOM order already gives for anyone not reading by sight. */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="order-1">
          <AccountGrowthCard series={series} canSee={canSeeRevenue} months={months} />
        </div>
        <div className="order-3 xl:order-2">
          <RevenueByPlanCard subscriptions={subscriptions} canSee={canSeeRevenue} />
        </div>
        <div className="order-4 xl:order-3">
          <AccountsByStatusCard orgs={orgs} />
        </div>
        <div className="order-2 xl:order-4 xl:col-span-3">
          <AccountsTable
            orgs={orgs}
            onRetry={retry}
            subscriptions={subscriptions}
            canSeeRevenue={canSeeRevenue}
            canWriteOverrides={canWriteOverrides}
            onView={openAccount}
            onPricing={openPricing}
            onSuspend={(org) => setRowAction({ kind: "suspend", org: toOrgRef(org) })}
            onReactivate={(org) => setRowAction({ kind: "reactivate", org: toOrgRef(org) })}
          />
        </div>
      </div>

      <section aria-label="Activity and attention" className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <RecentActivityCard audit={audit} orgNameById={orgNameById} canSee={canSeeAudit} />
        <TrialsEndingCard orgs={orgs} onOpen={openAccount} />
      </section>

      {rowAction?.kind === "suspend" ? (
        <SuspendAction
          org={rowAction.org}
          onClose={() => setRowAction(null)}
          onDone={(result) => {
            setBanner(result);
            setRowAction(null);
            retry();
          }}
        />
      ) : null}
      {rowAction?.kind === "reactivate" ? (
        <ReactivateAction
          org={rowAction.org}
          onClose={() => setRowAction(null)}
          onDone={(result) => {
            setBanner(result);
            setRowAction(null);
            retry();
          }}
        />
      ) : null}
    </div>
  );
}
