import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { listCustomers } from "@/api/client";
import type { CustomerSort } from "@/api/client";
import type { CustomerStanding, CustomerSummary } from "@/api/types";
import { formatDate } from "@/lib/format";
import { formatAmount } from "@/lib/money";
import { UsersIcon, cn } from "@opsgrids/ui";
import { useAsync } from "@opsgrids/client";
import {
  Avatar,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";

const SORTS: { value: CustomerSort; label: string }[] = [
  { value: "receivables_desc", label: "Receivables: high to low" },
  { value: "overdue_desc", label: "Overdue: high to low" },
  { value: "name_asc", label: "Name: A–Z" },
];

const STANDING: Record<CustomerStanding, { label: string; tone: string }> = {
  overdue: { label: "Overdue", tone: "bg-danger-soft text-danger" },
  open: { label: "Open balance", tone: "bg-info-soft text-info" },
  clear: { label: "Clear", tone: "bg-present-soft text-present" },
};

function StandingPill({ standing }: { standing: CustomerStanding }) {
  const s = STANDING[standing];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold",
        s.tone,
      )}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {s.label}
    </span>
  );
}

export function CustomersPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<CustomerSort>("receivables_desc");

  const loader = useCallback(() => listCustomers({ search, sort }), [search, sort]);
  const customers = useAsync(loader, [search, sort]);

  const rows = useMemo(
    () => (customers.status === "success" ? customers.data.items : []),
    [customers],
  );

  const columns: Column<CustomerSummary>[] = [
    {
      key: "name",
      header: "Customer",
      render: (row) => (
        <div className="flex items-center gap-3">
          <Avatar name={row.customer.name} size="md" />
          <div className="flex min-w-0 flex-col leading-tight">
            <span className="truncate font-bold text-ink">{row.customer.name}</span>
            <span className="truncate text-xs text-muted">
              {row.customer.city}, {row.customer.state}
            </span>
          </div>
        </div>
      ),
    },
    {
      key: "invoices",
      header: "Invoices",
      align: "right",
      cellClassName: "tnum text-muted hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (row) => row.invoice_count,
    },
    {
      key: "last",
      header: "Last invoice",
      cellClassName: "tnum text-muted whitespace-nowrap hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (row) => formatDate(row.last_invoice_date),
    },
    {
      key: "overdue",
      header: "Overdue",
      align: "right",
      cellClassName: "whitespace-nowrap hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (row) =>
        row.overdue_minor > 0 ? (
          <span className="money font-semibold text-danger">₹{formatAmount(row.overdue_minor)}</span>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "receivables",
      header: "Receivables",
      align: "right",
      cellClassName: "whitespace-nowrap",
      render: (row) =>
        row.receivables_minor > 0 ? (
          <span className="money font-semibold text-ink">₹{formatAmount(row.receivables_minor)}</span>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "standing",
      header: "Standing",
      align: "right",
      render: (row) => <StandingPill standing={row.standing} />,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Accounts receivable"
        title="Customers"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <UsersIcon className="size-4 text-muted" />
            <span className="tnum">{customers.status === "success" ? customers.data.total : "—"}</span>
            <span className="text-muted font-medium">accounts</span>
          </span>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <SearchInput
          label="Search customers"
          placeholder="Search by name or city…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          containerClassName="sm:max-w-xs"
        />
        <label className="inline-flex h-11 items-center gap-2 rounded-full bg-ground px-4 self-start sm:self-auto">
          <span className="text-xs font-semibold uppercase tracking-wide text-faint">Sort</span>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as CustomerSort)}
            className="h-full cursor-pointer bg-transparent text-sm font-semibold text-ink focus:outline-none"
          >
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </Card>

      <Card className="p-2 sm:p-4">
        {customers.status === "loading" ? (
          <div className="flex flex-col gap-2 p-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {customers.status === "error" ? <ErrorState message={customers.error.message} /> : null}
        {customers.status === "success" ? (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(row) => row.customer.id}
            onRowClick={(row) => navigate(`/invoices?q=${encodeURIComponent(row.customer.name)}`)}
            caption="Customers and their receivable position"
            emptyState={
              <EmptyState
                icon={<UsersIcon className="size-6" />}
                title="No customers match"
                description="Nothing matches that search. Try a different name or city."
              />
            }
          />
        ) : null}
      </Card>
    </div>
  );
}
