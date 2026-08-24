import { useCallback, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getCatalog, getOrganization } from "@/api/client";
import type {
  Entitlement,
  OrganizationDetail,
  OverrideResult,
  UsageSummary,
} from "@/api/types";
import { daysUntil, formatDate, formatInt, relativeDays } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import {
  AccountStatusPill,
  Avatar,
  Button,
  Card,
  DataTable,
  DefinitionList,
  ErrorState,
  InvoiceStatePill,
  LedgerKindPill,
  Money,
  SectionHeader,
  Skeleton,
  StatusPill,
  Tag,
} from "@/ui";
import type { Column, Definition } from "@/ui";
import {
  ArrowLeftIcon,
  CheckCircleIcon,
  GiftIcon,
  LockIcon,
  PauseIcon,
  PlayIcon,
  RepeatIcon,
  TagIcon,
} from "@/ui/icons";
import type { Subscription, Invoice, CreditLedgerEntry } from "@/api/types";
import {
  BespokePriceAction,
  CompGrantAction,
  MigratePlanAction,
  ReactivateAction,
  SuspendAction,
} from "./AccountActions";
import type { ActionKind } from "./AccountActions";

function EntitlementFlag({ entitlement }: { entitlement: Entitlement | undefined }) {
  if (!entitlement) return <span className="text-faint">—</span>;
  if (entitlement.entitled) {
    return (
      <span className="inline-flex items-center gap-1 text-sm font-semibold text-active">
        <CheckCircleIcon className="size-4" /> Entitled
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-sm font-semibold text-suspended">
      <LockIcon className="size-4" /> Blocked
    </span>
  );
}

function SubscriptionsCard({ detail }: { detail: OrganizationDetail }) {
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
            {s.is_bespoke ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-1.5 py-0.5 text-[0.65rem] font-semibold text-accent-ink">
                <TagIcon className="size-3" /> Bespoke
              </span>
            ) : null}
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
          <span className="text-pastdue">{relativeDays(s.trial_ends_at)}</span>
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

function UsageCard({ usage }: { usage: UsageSummary[] }) {
  return (
    <Card className="flex flex-col gap-4 p-6">
      <SectionHeader title="Usage" subtitle="Current period, metered per product" />
      {usage.length === 0 ? (
        <p className="py-4 text-sm text-muted">No metered usage recorded for this account.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {usage.map((u) => (
            <li key={u.meter_key} className="flex flex-wrap items-center gap-3 py-3">
              <div className="flex min-w-0 flex-col leading-tight">
                <span className="font-semibold text-ink">{u.meter_name}</span>
                <span className="mono text-xs text-faint">{u.meter_key}</span>
              </div>
              <div className="ml-auto flex items-center gap-3">
                {u.is_billable ? (
                  <span className="rounded-full bg-comp-soft px-2 py-0.5 text-xs font-semibold text-comp">
                    Billable
                  </span>
                ) : (
                  <Tag>Not billed</Tag>
                )}
                <span className="tnum text-right text-sm font-semibold text-ink">
                  {formatInt(u.quantity)} <span className="font-normal text-muted">{u.unit}</span>
                  {u.included !== null ? (
                    <span className="block text-xs font-normal text-faint">
                      of {formatInt(u.included)} included
                    </span>
                  ) : null}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function InvoicesCard({ invoices }: { invoices: Invoice[] }) {
  const columns: Column<Invoice>[] = [
    { key: "number", header: "Invoice", render: (i) => <span className="mono text-ink-soft">{i.number}</span> },
    {
      key: "period",
      header: "Period",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (i) => <span className="tnum text-muted">{formatDate(i.period_start)}</span>,
    },
    { key: "state", header: "State", render: (i) => <InvoiceStatePill state={i.state} /> },
    {
      key: "total",
      header: "Total",
      align: "right",
      render: (i) => <Money minor={i.total_minor} currency={i.currency} precise className="font-semibold" />,
    },
  ];
  return (
    <Card className="flex flex-col gap-4 p-6">
      <SectionHeader title="Invoices" subtitle="Append-only; frozen at finalize" />
      {invoices.length === 0 ? (
        <p className="py-4 text-sm text-muted">No invoices yet — this account has never been billed.</p>
      ) : (
        <DataTable columns={columns} rows={invoices} rowKey={(i) => i.invoice_id} caption="Invoices" dense />
      )}
    </Card>
  );
}

function LedgerCard({ detail }: { detail: OrganizationDetail }) {
  const columns: Column<CreditLedgerEntry>[] = [
    {
      key: "date",
      header: "Date",
      render: (e) => <span className="tnum text-muted">{formatDate(e.created_at)}</span>,
    },
    {
      key: "kind",
      header: "Entry",
      render: (e) => (
        <div className="flex items-center gap-2">
          <LedgerKindPill kind={e.kind} />
          {e.is_comp ? (
            <span className="rounded-full bg-comp-soft px-2 py-0.5 text-xs font-semibold text-comp">Comp</span>
          ) : null}
        </div>
      ),
    },
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
      render: (e) =>
        e.valid_until ? (
          <span className="tnum text-muted">{formatDate(e.valid_until)}</span>
        ) : (
          <span className="text-faint">no expiry</span>
        ),
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
        subtitle="Append-only grants, consumption and reversals"
        action={
          <div className="text-right">
            <span className="block text-xs font-semibold uppercase tracking-wide text-faint">Balance now</span>
            <Money
              minor={detail.comp_balance_minor}
              currency={detail.organization.currency}
              signed
              className="text-lg font-bold"
            />
          </div>
        }
      />
      {detail.ledger.length === 0 ? (
        <p className="py-4 text-sm text-muted">No credit or comp grants on this account.</p>
      ) : (
        <DataTable columns={columns} rows={detail.ledger} rowKey={(e) => e.entry_id} caption="Credit ledger" dense />
      )}
    </Card>
  );
}

export function AccountDetailPage() {
  const { orgId = "" } = useParams();
  const [reloadKey, setReloadKey] = useState(0);
  const [action, setAction] = useState<ActionKind>(null);
  const [banner, setBanner] = useState<OverrideResult | null>(null);

  const loader = useCallback(() => getOrganization(orgId), [orgId]);
  const detail = useAsync(loader, [orgId, reloadKey]);
  const catalog = useAsync(getCatalog, []);

  const catalogData = catalog.status === "success" ? catalog.data : null;

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
  const org = data.organization;
  const summary: Definition[] = [
    {
      term: "MRR",
      value:
        org.mrr_minor > 0 ? <Money minor={org.mrr_minor} currency={org.currency} /> : <span className="text-faint">—</span>,
    },
    { term: "Seats", value: <span className="tnum">{formatInt(org.seats)}</span> },
    {
      term: "Comp / credit balance",
      value: <Money minor={data.comp_balance_minor} currency={org.currency} signed />,
    },
    { term: "Customer since", value: formatDate(org.created_at) },
    { term: "Last activity", value: relativeDays(org.last_activity_at) },
    {
      term: "Primary contact",
      value: (
        <a href={`mailto:${org.primary_contact.email}`} className="text-accent hover:text-accent-strong">
          {org.primary_contact.name}
        </a>
      ),
    },
  ];

  const trialLeft = org.status === "trial" ? daysUntil(org.trial_ends_at) : null;

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
            <Avatar name={org.name} size="lg" />
            <div className="flex flex-col gap-1.5">
              <h1 className="display text-2xl font-extrabold leading-tight sm:text-3xl">{org.name}</h1>
              <div className="flex flex-wrap items-center gap-2">
                <span className="mono text-sm text-faint">{org.slug}</span>
                <span className="text-faint">·</span>
                <span className="text-sm text-muted">{org.region}</span>
              </div>
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <AccountStatusPill status={org.status} />
                {org.is_bespoke ? <StatusPill tone="accent">Bespoke pricing</StatusPill> : null}
                {org.locked ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-suspended-soft px-2.5 py-1 text-xs font-semibold text-suspended">
                    <LockIcon className="size-3.5" /> Access locked
                  </span>
                ) : null}
                {trialLeft !== null ? (
                  <span className="text-xs font-semibold text-pastdue">
                    Trial ends {relativeDays(org.trial_ends_at)}
                  </span>
                ) : null}
              </div>
            </div>
          </div>

          {/* Operator actions */}
          <div className="flex flex-wrap items-center gap-2">
            {org.status === "suspended" ? (
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
            <Button variant="outline" leadingIcon={<TagIcon className="size-4" />} onClick={() => setAction("bespoke")}>
              Bespoke price
            </Button>
            <Button variant="outline" leadingIcon={<RepeatIcon className="size-4" />} onClick={() => setAction("migrate")}>
              Migrate plan
            </Button>
          </div>
        </div>
      </div>

      {banner ? (
        <div
          role="status"
          className="flex items-start gap-3 rounded-card bg-active-soft px-4 py-3 text-sm text-active"
        >
          <CheckCircleIcon className="mt-0.5 size-5 shrink-0" />
          <div className="flex flex-col">
            <span className="font-semibold">Override accepted</span>
            <span className="text-active/80">{banner.audit.summary}</span>
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
        <DefinitionList items={summary} className="sm:grid-cols-3" />
      </Card>

      <SubscriptionsCard detail={data} />

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <UsageCard usage={data.usage} />
        <InvoicesCard invoices={data.invoices} />
      </div>

      <LedgerCard detail={data} />

      {/* Action modals */}
      {action === "suspend" ? (
        <SuspendAction org={org} onClose={() => setAction(null)} onDone={handleDone} />
      ) : null}
      {action === "reactivate" ? (
        <ReactivateAction org={org} onClose={() => setAction(null)} onDone={handleDone} />
      ) : null}
      {action === "comp" ? (
        <CompGrantAction org={org} onClose={() => setAction(null)} onDone={handleDone} />
      ) : null}
      {action === "bespoke" ? (
        <BespokePriceAction org={org} catalog={catalogData} onClose={() => setAction(null)} onDone={handleDone} />
      ) : null}
      {action === "migrate" ? (
        <MigratePlanAction
          org={org}
          subscriptions={data.subscriptions}
          catalog={catalogData}
          onClose={() => setAction(null)}
          onDone={handleDone}
        />
      ) : null}
    </div>
  );
}
