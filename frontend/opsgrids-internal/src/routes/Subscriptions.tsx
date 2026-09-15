import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { listSubscriptions } from "@/api/client";
import type { AccountStatus, Subscription } from "@/api/types";
import { formatDate, formatInt, relativeDays } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import {
  AccountStatusPill,
  Card,
  DataTable,
  EmptyState,
  EnvBadge,
  ErrorState,
  Money,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { LayersIcon, TagIcon } from "@/ui/icons";

type StateFilter = AccountStatus | "all";

const STATE_FILTERS: { value: StateFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "trial", label: "Trial" },
  { value: "past_due", label: "Past due" },
  { value: "suspended", label: "Suspended" },
  { value: "cancelled", label: "Cancelled" },
];

const NO_ROWS: Subscription[] = [];

export function SubscriptionsPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [state, setState] = useState<StateFilter>("all");

  const loader = useCallback(() => listSubscriptions({ search, state }), [search, state]);
  const subs = useAsync(loader, [search, state]);

  const rows = useMemo(
    () => (subs.status === "success" ? subs.data.items : NO_ROWS),
    [subs],
  );

  const columns: Column<Subscription>[] = [
    {
      key: "org",
      header: "Organization",
      render: (s) => (
        <Link
          to={`/accounts/${s.organization_id}`}
          className="font-bold text-ink hover:text-accent transition-colors"
          onClick={(e) => e.stopPropagation()}
        >
          {s.organization_name}
        </Link>
      ),
    },
    {
      key: "product",
      header: "Product · plan",
      render: (s) => (
        <div className="flex items-center gap-1.5">
          <span className="font-semibold text-ink">{s.product_name}</span>
          <span className="text-muted">·</span>
          <span className="text-muted">{s.plan_name}</span>
          {s.is_bespoke ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-1.5 py-0.5 text-[0.65rem] font-semibold text-accent-ink">
              <TagIcon className="size-3" /> Bespoke
            </span>
          ) : null}
        </div>
      ),
    },
    { key: "state", header: "State", render: (s) => <AccountStatusPill status={s.state} /> },
    {
      key: "seats",
      header: "Seats",
      align: "right",
      cellClassName: "hidden md:table-cell tnum",
      headerClassName: "hidden md:table-cell",
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
      header: "Next renewal / ends",
      align: "right",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (s) =>
        s.state === "trial" ? (
          <span className="text-pastdue">trial · {relativeDays(s.trial_ends_at)}</span>
        ) : s.next_renewal_at ? (
          <span className="tnum text-muted">{formatDate(s.next_renewal_at)}</span>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Cross-tenant"
        title="Subscriptions"
        description="Every subscription across all tenants, with its lifecycle state and next renewal."
        actions={<EnvBadge />}
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search subscriptions"
            placeholder="Search by organization, product or plan…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <div className="-mx-1 flex items-center gap-1.5 overflow-x-auto px-1">
            {STATE_FILTERS.map((f) => (
              <Pill key={f.value} active={state === f.value} onClick={() => setState(f.value)}>
                {f.label}
              </Pill>
            ))}
          </div>
        </div>
      </Card>

      <Card className="p-4 sm:p-5">
        {subs.status === "loading" ? (
          <div className="flex flex-col gap-2 py-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : subs.status === "error" ? (
          <ErrorState message={subs.error.message} />
        ) : (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(s) => s.subscription_id}
            caption="Subscriptions across all tenants"
            dense
            onRowClick={(s) => navigate(`/accounts/${s.organization_id}`)}
            emptyState={
              <EmptyState
                icon={<LayersIcon className="size-6" />}
                title="No matching subscriptions"
                description="Nothing matches these filters. Try a different lifecycle state or clear the search."
              />
            }
          />
        )}
      </Card>
    </div>
  );
}
