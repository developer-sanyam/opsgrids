import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { listCompanies } from "@/api/client";
import type { Company, CompanyStatus } from "@/api/types";
import { contactsForCompany, ownerName } from "@/data/lookups";
import { formatMoneyCompact } from "@/lib/format";
import { useAsync } from "@opsgrids/client";
import {
  Avatar,
  Card,
  CompanyStatusPill,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { BuildingIcon, GlobeIcon, MapPinIcon } from "@opsgrids/ui";

type StatusFilter = CompanyStatus | "all";
type Sort = "name" | "revenue" | "contacts";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "customer", label: "Customers" },
  { value: "prospect", label: "Prospects" },
  { value: "lead", label: "Leads" },
  { value: "churned", label: "Churned" },
];

const NO_COMPANIES: Company[] = [];

const columns: Column<Company>[] = [
  {
    key: "name",
    header: "Company",
    render: (c) => (
      <div className="flex items-center gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-tile bg-accent-soft text-accent-ink">
          <BuildingIcon className="size-4" />
        </span>
        <div className="flex min-w-0 flex-col leading-tight">
          <Link
            to={`/companies/${c.id}`}
            className="truncate font-semibold text-ink hover:text-accent transition-colors"
          >
            {c.name}
          </Link>
          <span className="inline-flex items-center gap-1 truncate text-xs text-muted">
            <GlobeIcon className="size-3" />
            {c.domain}
          </span>
        </div>
      </div>
    ),
  },
  {
    key: "industry",
    header: "Industry",
    cellClassName: "hidden md:table-cell",
    headerClassName: "hidden md:table-cell",
    render: (c) => <span className="text-ink-soft">{c.industry}</span>,
  },
  {
    key: "location",
    header: "Location",
    cellClassName: "hidden lg:table-cell",
    headerClassName: "hidden lg:table-cell",
    render: (c) => (
      <span className="inline-flex items-center gap-1.5 text-muted">
        <MapPinIcon className="size-4 text-faint" />
        {c.city}
      </span>
    ),
  },
  { key: "status", header: "Status", render: (c) => <CompanyStatusPill status={c.status} /> },
  {
    key: "owner",
    header: "Owner",
    cellClassName: "hidden sm:table-cell",
    headerClassName: "hidden sm:table-cell",
    render: (c) => (
      <span className="inline-flex items-center gap-2 min-w-0">
        <Avatar name={ownerName(c.owner_id)} size="sm" />
        <span className="truncate text-sm text-ink-soft">{ownerName(c.owner_id)}</span>
      </span>
    ),
  },
  {
    key: "revenue",
    header: "Annual revenue",
    align: "right",
    render: (c) => (
      <span className="tnum font-semibold text-ink">
        {formatMoneyCompact(c.annual_revenue_minor, c.currency)}
      </span>
    ),
  },
];

export function CompaniesPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [sort, setSort] = useState<Sort>("name");

  const loader = useCallback(() => listCompanies({ search, status }), [search, status]);
  const companies = useAsync(loader, [search, status]);

  const rows = useMemo(() => {
    const items = companies.status === "success" ? companies.data.items : NO_COMPANIES;
    const sorted = [...items];
    if (sort === "name") sorted.sort((a, b) => a.name.localeCompare(b.name));
    else if (sort === "revenue")
      sorted.sort((a, b) => b.annual_revenue_minor - a.annual_revenue_minor);
    else sorted.sort((a, b) => contactsForCompany(b.id).length - contactsForCompany(a.id).length);
    return sorted;
  }, [companies, sort]);

  const total = companies.status === "success" ? companies.data.total : 0;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Records"
        title="Companies"
        actions={
          <span className="inline-flex h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold shadow-card">
            <BuildingIcon className="size-4 text-muted" />
            <span className="tnum">{total}</span>
            <span className="font-medium text-muted">accounts</span>
          </span>
        }
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search companies"
            placeholder="Search by name, industry or city…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-xs"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            {STATUS_FILTERS.map((f) => (
              <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
                {f.label}
              </Pill>
            ))}
          </div>
          <label className="ml-auto inline-flex h-11 shrink-0 items-center rounded-full bg-ground px-4">
            <span className="sr-only">Sort companies</span>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as Sort)}
              className="h-full cursor-pointer bg-transparent pr-1 text-sm font-semibold text-ink focus:outline-none"
            >
              <option value="name">Name A–Z</option>
              <option value="revenue">Revenue high–low</option>
              <option value="contacts">Most contacts</option>
            </select>
          </label>
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {companies.status === "loading" ? (
          <div className="flex flex-col gap-3 p-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {companies.status === "error" ? <ErrorState message={companies.error.message} /> : null}
        {companies.status === "success" ? (
          <DataTable
            caption="All companies"
            columns={columns}
            rows={rows}
            rowKey={(c) => c.id}
            emptyState={
              <EmptyState
                icon={<BuildingIcon className="size-6" />}
                title="No matching companies"
                description="No accounts match this search or status. Clear the filters to see them all."
              />
            }
          />
        ) : null}
      </Card>
    </div>
  );
}
