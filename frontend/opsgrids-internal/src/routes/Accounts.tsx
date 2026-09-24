import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { BuildingIcon, LockIcon, TagIcon } from "@opsgrids/ui";

import { listOrganizations } from "@/api/client";
import type { AccountStatus, OrganizationRow } from "@/api/types";
import { formatDate, formatInt, relativeDays } from "@/lib/format";
import {
  AccountStatusPill,
  Avatar,
  BespokeTag,
  Card,
  DataTable,
  EmptyState,
  EnvBadge,
  LoadFailure,
  Money,
  PageHeader,
  Pill,
  SearchInput,
  Select,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";

type StatusFilter = AccountStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "trial", label: "Trial" },
  { value: "past_due", label: "Past due" },
  { value: "suspended", label: "Suspended" },
  { value: "cancelled", label: "Cancelled" },
];

type SortKey = "mrr_desc" | "name_asc" | "created_desc" | "seats_desc";

const SORTS: { value: SortKey; label: string }[] = [
  { value: "mrr_desc", label: "MRR (high → low)" },
  { value: "name_asc", label: "Name (A → Z)" },
  { value: "created_desc", label: "Newest first" },
  { value: "seats_desc", label: "Seats (high → low)" },
];

function sortRows(rows: OrganizationRow[], key: SortKey): OrganizationRow[] {
  const copy = [...rows];
  switch (key) {
    case "mrr_desc":
      return copy.sort((a, b) => b.mrr_minor - a.mrr_minor || a.name.localeCompare(b.name));
    case "name_asc":
      return copy.sort((a, b) => a.name.localeCompare(b.name));
    case "created_desc":
      return copy.sort((a, b) => b.created_at.localeCompare(a.created_at));
    case "seats_desc":
      return copy.sort((a, b) => b.seats - a.seats);
    default:
      return copy;
  }
}

const NO_ROWS: OrganizationRow[] = [];

export function AccountsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [bespoke, setBespoke] = useState(false);
  const [sort, setSort] = useState<SortKey>("mrr_desc");

  // The sidebar's search lands here with `?q=`, including while this screen is already open.
  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);

  const loader = useCallback(
    () => listOrganizations({ search, status, ...(bespoke ? { bespoke: true } : {}) }),
    [search, status, bespoke],
  );
  const accounts = useAsync(loader, [search, status, bespoke]);

  const rows = useMemo(() => {
    const items = accounts.status === "success" ? accounts.data.items : NO_ROWS;
    return sortRows(items, sort);
  }, [accounts, sort]);

  const totalMrr = useMemo(() => rows.reduce((sum, r) => sum + r.mrr_minor, 0), [rows]);

  const columns: Column<OrganizationRow>[] = [
    {
      key: "org",
      header: "Organization",
      render: (row) => (
        <div className="flex items-center gap-3">
          <Avatar name={row.name} size="sm" shape="square" />
          <div className="flex min-w-0 flex-col leading-tight">
            <Link
              to={`/accounts/${row.organization_id}`}
              className="truncate font-bold text-ink transition-colors hover:text-accent"
              onClick={(e) => e.stopPropagation()}
            >
              {row.name}
            </Link>
            <span className="money truncate text-xs text-faint">{row.slug}</span>
          </div>
        </div>
      ),
    },
    {
      key: "product",
      header: "Product · plan",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (row) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{row.primary_product_name}</span>
          <span className="flex items-center gap-1.5 text-xs text-muted">
            {row.plan_name}
            {row.is_bespoke ? <BespokeTag /> : null}
          </span>
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (row) => (
        <div className="flex items-center gap-2">
          <AccountStatusPill status={row.status} />
          {row.locked ? (
            <span
              className="inline-flex items-center gap-1 text-xs font-semibold text-danger"
              title="Access locked — entitlement lapsed to a 402"
            >
              <LockIcon className="size-3.5" />
              <span className="sr-only">Access locked</span>
            </span>
          ) : null}
        </div>
      ),
    },
    {
      key: "mrr",
      header: "MRR",
      align: "right",
      render: (row) =>
        row.mrr_minor > 0 ? (
          <Money minor={row.mrr_minor} currency={row.currency} className="font-semibold" />
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "seats",
      header: "Seats",
      align: "right",
      cellClassName: "hidden lg:table-cell tnum",
      headerClassName: "hidden lg:table-cell",
      render: (row) => <span className="tnum text-ink-soft">{formatInt(row.seats)}</span>,
    },
    {
      key: "created",
      header: "Created",
      cellClassName: "hidden xl:table-cell",
      headerClassName: "hidden xl:table-cell",
      render: (row) => <span className="tnum text-muted">{formatDate(row.created_at)}</span>,
    },
    {
      key: "activity",
      header: "Last activity",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (row) => <span className="text-muted">{relativeDays(row.last_activity_at)}</span>,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Cross-tenant"
        title="Accounts"
        description="Every customer organization, its plan, lifecycle state and revenue. Select a row to drill in."
        actions={<EnvBadge />}
      />

      {/* Controls */}
      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
          <SearchInput
            label="Search accounts"
            placeholder="Search by name, slug, product or region…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <div className="-mx-1 flex items-center gap-1.5 overflow-x-auto px-1" role="group" aria-label="Status">
            {STATUS_FILTERS.map((f) => (
              <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
                {f.label}
              </Pill>
            ))}
          </div>
          <Select
            label="Sort"
            options={SORTS}
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            containerClassName="lg:ml-auto lg:w-56"
          />
        </div>
        <div className="flex items-center gap-2 border-t border-line pt-4">
          <Pill active={bespoke} leadingIcon={<TagIcon className="size-4" />} onClick={() => setBespoke((v) => !v)}>
            Bespoke pricing only
          </Pill>
        </div>
      </Card>

      {/* Table */}
      <Card className="flex flex-col gap-2 p-4 sm:p-5">
        {accounts.status === "success" ? (
          <div role="status" className="flex flex-wrap items-center justify-between gap-2 px-1 pb-2">
            <span className="text-sm text-muted">
              <span className="tnum font-semibold text-ink">{rows.length}</span>{" "}
              {rows.length === 1 ? "account" : "accounts"}
            </span>
            <span className="text-sm text-muted">
              Combined MRR <Money minor={totalMrr} currency="INR" className="font-semibold text-ink" />
            </span>
          </div>
        ) : null}

        {accounts.status === "loading" ? (
          <div className="flex flex-col gap-2 py-2" aria-busy>
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}

        {accounts.status === "error" ? (
          <LoadFailure error={accounts.error} title="Couldn't load accounts" onRetry={accounts.reload} />
        ) : null}

        {accounts.status === "success" ? (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(row) => row.organization_id}
            caption="Customer organizations across all tenants"
            dense
            onRowClick={(row) => navigate(`/accounts/${row.organization_id}`)}
            emptyState={
              <EmptyState
                icon={<BuildingIcon className="size-6" />}
                title="No matching accounts"
                description="No organization matches these filters. Try a different status, clear the bespoke filter, or adjust the search."
              />
            }
          />
        ) : null}
      </Card>
    </div>
  );
}
