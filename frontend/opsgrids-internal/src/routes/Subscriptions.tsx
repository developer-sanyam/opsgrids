import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { listSubscriptions } from "@/api/client";
import type { SubscriptionOut } from "@/api/types";
import { formatDate } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import {
  AccountStatusPill,
  Card,
  DataTable,
  EmptyState,
  EnvBadge,
  ErrorState,
  Money,
  OrgIdentity,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { LayersIcon } from "@/ui/icons";

const NO_ROWS: SubscriptionOut[] = [];

export function SubscriptionsPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [state, setState] = useState<string>("all");

  const subs = useAsync(() => listSubscriptions(), []);

  const all = subs.status === "success" ? subs.data.subscriptions : NO_ROWS;
  const states = useMemo(() => [...new Set(all.map((s) => s.state))].sort(), [all]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return all.filter((s) => {
      const matchQuery =
        q === "" ||
        s.organization_id.toLowerCase().includes(q) ||
        (s.name?.toLowerCase().includes(q) ?? false) ||
        (s.slug?.toLowerCase().includes(q) ?? false) ||
        s.product_key.toLowerCase().includes(q);
      const matchState = state === "all" || s.state === state;
      return matchQuery && matchState;
    });
  }, [all, search, state]);

  const columns: Column<SubscriptionOut>[] = [
    {
      key: "org",
      header: "Organization",
      render: (s) => (
        <Link
          to={`/accounts/${s.organization_id}`}
          className="block truncate hover:text-accent transition-colors"
          onClick={(e) => e.stopPropagation()}
        >
          <OrgIdentity name={s.name} slug={s.slug} organizationId={s.organization_id} />
        </Link>
      ),
    },
    {
      key: "product",
      header: "Product",
      render: (s) => <span className="font-semibold text-ink">{s.product_key}</span>,
    },
    { key: "state", header: "State", render: (s) => <AccountStatusPill status={s.state} /> },
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
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Cross-tenant"
        title="Subscriptions"
        description="Every subscription across all tenants, flattened from each organization's commercial snapshot."
        actions={<EnvBadge />}
      />

      <p className="text-xs text-faint">
        No plan name, seat count or renewal date is in this projection yet — only the plan version
        id, amount and state.
      </p>

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search subscriptions"
            placeholder="Search by name, slug, organization id or product…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <div className="-mx-1 flex items-center gap-1.5 overflow-x-auto px-1">
            <Pill active={state === "all"} onClick={() => setState("all")}>
              All
            </Pill>
            {states.map((s) => (
              <Pill key={s} active={state === s} onClick={() => setState(s)}>
                {s}
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
            rowKey={(s) => `${s.organization_id}:${s.product_key}`}
            caption="Subscriptions across all tenants"
            dense
            onRowClick={(s) => navigate(`/accounts/${s.organization_id}`)}
            emptyState={
              <EmptyState
                icon={<LayersIcon className="size-6" />}
                title="No matching subscriptions"
                description="Nothing matches these filters. Try a different state or clear the search."
              />
            }
          />
        )}
      </Card>
    </div>
  );
}
