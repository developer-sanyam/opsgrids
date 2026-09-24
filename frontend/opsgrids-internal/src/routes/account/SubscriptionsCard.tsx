import { useMemo } from "react";

import { CheckCircleIcon, LockIcon } from "@opsgrids/ui";

import type { Entitlement, OrganizationDetail, Subscription } from "@/api/types";
import { formatDate, formatInt, relativeDays } from "@/lib/format";
import { AccountStatusPill, BespokeTag, Card, DataTable, Money, SectionHeader } from "@/ui";
import type { Column } from "@/ui";

function EntitlementFlag({ entitlement }: { entitlement: Entitlement | undefined }) {
  if (!entitlement) return <span className="text-faint">—</span>;
  if (entitlement.entitled) {
    return (
      <span className="inline-flex items-center gap-1 text-sm font-semibold text-present">
        <CheckCircleIcon className="size-4" /> Entitled
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-sm font-semibold text-danger">
      <LockIcon className="size-4" /> Blocked
    </span>
  );
}

/** Pinned plan versions and access state, one row per subscription. */
export function SubscriptionsCard({ detail }: { detail: OrganizationDetail }) {
  const entByProduct = useMemo(
    () => new Map(detail.entitlements.map((e) => [e.product_key, e])),
    [detail.entitlements],
  );

  const columns: Column<Subscription>[] = [
    {
      key: "product",
      header: "Product · plan",
      render: (s) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{s.product_name}</span>
          <span className="flex items-center gap-1.5 text-xs text-muted">
            {s.plan_name} · v{s.plan_version_no}
            {s.is_bespoke ? <BespokeTag /> : null}
          </span>
        </div>
      ),
    },
    { key: "state", header: "State", render: (s) => <AccountStatusPill status={s.state} /> },
    {
      key: "access",
      header: "Access",
      render: (s) => <EntitlementFlag entitlement={entByProduct.get(s.product_key)} />,
    },
    {
      key: "seats",
      header: "Seats",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (s) => <span className="tnum text-ink-soft">{formatInt(s.seats)}</span>,
    },
    {
      key: "amount",
      header: "Per month",
      align: "right",
      render: (s) =>
        s.amount_minor > 0 ? (
          <Money minor={s.amount_minor} currency={s.currency} className="font-semibold" />
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "renewal",
      header: "Renews / ends",
      align: "right",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (s) =>
        s.state === "trial" ? (
          <span className="text-late">{relativeDays(s.trial_ends_at)}</span>
        ) : s.next_renewal_at ? (
          <span className="tnum text-muted">{formatDate(s.next_renewal_at)}</span>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
  ];

  return (
    <Card className="flex flex-col gap-4 p-6">
      <SectionHeader title="Subscriptions & entitlements" subtitle="Pinned plan versions and access state" />
      <DataTable
        columns={columns}
        rows={detail.subscriptions}
        rowKey={(s) => s.subscription_id}
        caption="Subscriptions for this organization"
      />
    </Card>
  );
}
