import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getOrganization, listLedger, listSubscriptions, listUsage } from "@/api/client";
import type {
  CreditLedgerEntryAdminOut,
  OrgDetailOut,
  OverrideResult,
  SubscriptionOut,
  UsageOut,
} from "@/api/types";
import { formatDate, formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import {
  AccountStatusPill,
  Avatar,
  Button,
  Card,
  DataTable,
  DefinitionList,
  ErrorState,
  LedgerKindPill,
  Money,
  SectionHeader,
  Skeleton,
  Tag,
} from "@/ui";
import type { Column, Definition } from "@/ui";
import { ArrowLeftIcon, CheckCircleIcon, GiftIcon, LockIcon, PauseIcon, PlayIcon } from "@/ui/icons";
import { ClientPricingCard } from "./AccountPricing";
import { CompGrantAction, ReactivateAction, SuspendAction } from "./AccountActions";
import type { ActionKind } from "./AccountActions";

function SubscriptionsCard({ orgId, reloadKey }: { orgId: string; reloadKey: number }) {
  const loader = useCallback(async (): Promise<SubscriptionOut[]> => {
    const all = await listSubscriptions();
    return all.subscriptions.filter((s) => s.organization_id === orgId);
  }, [orgId]);
  const subs = useAsync(loader, [orgId, reloadKey]);

  const columns: Column<SubscriptionOut>[] = [
    { key: "product", header: "Product", render: (s) => <span className="font-semibold text-ink">{s.product_key}</span> },
    { key: "state", header: "State", render: (s) => <AccountStatusPill status={s.state} /> },
    {
      key: "plan_version",
      header: "Plan version",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (s) => <span className="mono truncate text-xs text-faint">{s.plan_version_id ?? "—"}</span>,
    },
    {
      key: "amount",
      header: "Per month",
      align: "right",
      render: (s) =>
        s.amount_minor > 0 ? (
          <Money minor={s.amount_minor} currency={s.currency ?? "INR"} className="font-semibold" />
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "trial",
      header: "Trial ends",
      align: "right",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (s) => <span className="tnum text-muted">{s.trial_ends_at ? formatDate(s.trial_ends_at) : "—"}</span>,
    },
  ];

  return (
    <Card className="flex flex-col gap-4 p-6">
      <SectionHeader
        title="Subscriptions"
        subtitle="Flattened from the commercial snapshot — no seat count or renewal date in this projection"
      />
      {subs.status === "loading" ? (
        <Skeleton className="h-32 w-full" />
      ) : subs.status === "error" ? (
        <ErrorState message={subs.error.message} />
      ) : subs.data.length === 0 ? (
        <p className="py-4 text-sm text-muted">No subscription recorded for this organization.</p>
      ) : (
        <DataTable columns={columns} rows={subs.data} rowKey={(s) => `${s.organization_id}:${s.product_key}`} caption="Subscriptions for this organization" />
      )}
    </Card>
  );
}

function UsageCard({ orgId, reloadKey }: { orgId: string; reloadKey: number }) {
  const loader = useCallback(async (): Promise<UsageOut[]> => {
    const all = await listUsage();
    return all.usage.filter((u) => u.organization_id === orgId);
  }, [orgId]);
  const usage = useAsync(loader, [orgId, reloadKey]);

  return (
    <Card className="flex flex-col gap-4 p-6">
      <SectionHeader title="Usage" subtitle="Raw meter quantities — no unit, allowance or billability in this projection" />
      {usage.status === "loading" ? (
        <Skeleton className="h-32 w-full" />
      ) : usage.status === "error" ? (
        <ErrorState message={usage.error.message} />
      ) : usage.data.length === 0 ? (
        <p className="py-4 text-sm text-muted">No metered usage recorded for this account.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {usage.data.map((u) => (
            <li key={u.meter_key} className="flex flex-wrap items-center gap-3 py-3">
              <span className="mono text-sm font-semibold text-ink">{u.meter_key}</span>
              <span className="text-xs text-faint">{u.period}</span>
              <span className="tnum ml-auto text-sm font-semibold text-ink">{u.quantity}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function LedgerCard({ orgId, reloadKey }: { orgId: string; reloadKey: number }) {
  const loader = useCallback(
    () => listLedger({ organizationId: orgId, size: 200 }),
    [orgId],
  );
  const ledger = useAsync(loader, [orgId, reloadKey]);

  const columns: Column<CreditLedgerEntryAdminOut>[] = [
    { key: "date", header: "Date", render: (e) => <span className="tnum text-muted">{formatDate(e.created_at)}</span> },
    { key: "kind", header: "Entry", render: (e) => <LedgerKindPill kind={e.entry_type} /> },
    {
      key: "reason",
      header: "Reason",
      cellClassName: "hidden md:table-cell max-w-xs",
      headerClassName: "hidden md:table-cell",
      render: (e) => <span className="text-muted">{e.reason}</span>,
    },
    {
      key: "window",
      header: "Valid until",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (e) => (e.valid_until ? <span className="tnum text-muted">{formatDate(e.valid_until)}</span> : <span className="text-faint">no expiry</span>),
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      render: (e) => <Money minor={e.amount_minor} currency={e.currency} signed className="font-semibold" />,
    },
  ];

  return (
    <Card className="flex flex-col gap-4 p-6">
      <SectionHeader
        title="Credit ledger"
        subtitle="Append-only grants, consumption, reversals and expiries — no running balance is exposed to the console"
      />
      {ledger.status === "loading" ? (
        <Skeleton className="h-32 w-full" />
      ) : ledger.status === "error" ? (
        <ErrorState message={ledger.error.message} />
      ) : ledger.data.items.length === 0 ? (
        <p className="py-4 text-sm text-muted">No credit or comp grants on this account.</p>
      ) : (
        <DataTable columns={columns} rows={ledger.data.items} rowKey={(e) => e.id} caption="Credit ledger" dense />
      )}
    </Card>
  );
}

function summaryFor(data: OrgDetailOut): Definition[] {
  const commercial = data.commercial;
  return [
    {
      term: "MRR",
      value: commercial && commercial.mrr_minor > 0 ? (
        <Money minor={commercial.mrr_minor} currency={commercial.currency ?? "INR"} />
      ) : (
        <span className="text-faint">—</span>
      ),
    },
    {
      term: "Active products",
      value:
        commercial && commercial.active_products.length > 0 ? (
          <span className="flex flex-wrap gap-1">
            {commercial.active_products.map((p) => (
              <Tag key={p}>{p}</Tag>
            ))}
          </span>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    { term: "Customer since", value: formatDate(data.created_at) },
    {
      term: "Commercial data updated",
      value: commercial ? formatDateTime(commercial.updated_at) : <span className="text-faint">not yet projected</span>,
    },
    { term: "Country", value: data.country ?? <span className="text-faint">—</span> },
  ];
}

export function AccountDetailPage() {
  const { orgId = "" } = useParams();
  const [reloadKey, setReloadKey] = useState(0);
  const [action, setAction] = useState<ActionKind>(null);
  const [banner, setBanner] = useState<OverrideResult | null>(null);

  const loader = useCallback(() => getOrganization(orgId), [orgId]);
  const detail = useAsync(loader, [orgId, reloadKey]);

  const handleDone = useCallback((result: OverrideResult) => {
    setBanner(result);
    setReloadKey((k) => k + 1);
  }, []);

  if (detail.status === "loading") {
    return (
      <div className="flex flex-col gap-5">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (detail.status === "error") {
    return (
      <Card className="p-6">
        <ErrorState message={detail.error.message} onRetry={() => setReloadKey((k) => k + 1)} />
      </Card>
    );
  }

  const data = detail.data;
  const locked = data.commercial?.locked ?? false;
  const trialEndsAt = data.commercial?.trial_ends_at ?? null;
  const pricingOrg = { organization_id: data.organization_id, name: data.name };

  return (
    <div className="rise flex flex-col gap-5">
      <div className="flex flex-col gap-4">
        <Link
          to="/accounts"
          className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
        >
          <ArrowLeftIcon className="size-4" />
          All accounts
        </Link>

        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-4">
            <Avatar name={data.name} size="lg" />
            <div className="flex flex-col gap-1.5">
              <h1 className="display text-2xl font-extrabold leading-tight sm:text-3xl">{data.name}</h1>
              <div className="flex flex-wrap items-center gap-2">
                <span className="mono text-sm text-faint">{data.slug}</span>
                {data.country ? (
                  <>
                    <span className="text-faint">·</span>
                    <span className="text-sm text-muted">{data.country}</span>
                  </>
                ) : null}
              </div>
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <AccountStatusPill status={data.status} />
                {locked ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-suspended-soft px-2.5 py-1 text-xs font-semibold text-suspended">
                    <LockIcon className="size-3.5" /> Access locked
                  </span>
                ) : null}
                {trialEndsAt ? (
                  <span className="text-xs font-semibold text-pastdue">Trial ends {formatDate(trialEndsAt)}</span>
                ) : null}
              </div>
            </div>
          </div>

          {/* Operator actions */}
          <div className="flex flex-wrap items-center gap-2">
            {locked ? (
              <Button variant="outline" leadingIcon={<PlayIcon className="size-4" />} onClick={() => setAction("reactivate")}>
                Reactivate
              </Button>
            ) : (
              <Button variant="danger" leadingIcon={<PauseIcon className="size-4" />} onClick={() => setAction("suspend")}>
                Suspend
              </Button>
            )}
            <Button variant="outline" leadingIcon={<GiftIcon className="size-4" />} onClick={() => setAction("comp")}>
              Grant credit
            </Button>
          </div>
        </div>
      </div>

      {banner ? (
        <div role="status" className="flex items-start gap-3 rounded-card bg-active-soft px-4 py-3 text-sm text-active">
          <CheckCircleIcon className="mt-0.5 size-5 shrink-0" />
          <div className="flex flex-col">
            <span className="font-semibold">Override accepted</span>
            <span className="text-active/80">Delegated to {banner.delegated_to}-service.</span>
          </div>
          <button
            type="button"
            onClick={() => setBanner(null)}
            className="ml-auto text-xs font-semibold text-active/70 hover:text-active"
          >
            Dismiss
          </button>
        </div>
      ) : null}

      <Card className="p-6">
        <DefinitionList items={summaryFor(data)} className="sm:grid-cols-3" />
      </Card>

      <SubscriptionsCard orgId={data.organization_id} reloadKey={reloadKey} />

      <ClientPricingCard org={pricingOrg} />

      <UsageCard orgId={data.organization_id} reloadKey={reloadKey} />

      <LedgerCard orgId={data.organization_id} reloadKey={reloadKey} />

      {/* Action modals */}
      {action === "suspend" ? (
        <SuspendAction org={pricingOrg} onClose={() => setAction(null)} onDone={handleDone} />
      ) : null}
      {action === "reactivate" ? (
        <ReactivateAction org={pricingOrg} onClose={() => setAction(null)} />
      ) : null}
      {action === "comp" ? (
        <CompGrantAction org={pricingOrg} onClose={() => setAction(null)} onDone={handleDone} />
      ) : null}
    </div>
  );
}
