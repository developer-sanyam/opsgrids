import { useEffect, useMemo, useState } from "react";
import { MobileDataCard } from "@opsgrids/ui";
import type { MobileCardWiring } from "@opsgrids/ui";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { listOrganizations } from "@/api/client";
import type { OrgStateOut } from "@/api/types";
import { formatDate, orgIdentity, relativeDays } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import {
  Card,
  DataTable,
  EmptyState,
  EnvBadge,
  ErrorState,
  OrgIdentity,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
  Tag,
  Money,
} from "@/ui";
import type { Column } from "@/ui";
import { BuildingsIcon, LockIcon } from "@/ui/icons";

type StatusFilter = "all" | "locked" | "trial";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "trial", label: "In trial" },
  { value: "locked", label: "Locked" },
];

type SortKey = "mrr_desc" | "id_asc" | "updated_desc";

const SORTS: { value: SortKey; label: string }[] = [
  { value: "mrr_desc", label: "MRR (high → low)" },
  { value: "updated_desc", label: "Recently updated" },
  { value: "id_asc", label: "Organization ID (A → Z)" },
];

function sortRows(rows: OrgStateOut[], key: SortKey): OrgStateOut[] {
  const copy = [...rows];
  switch (key) {
    case "mrr_desc":
      return copy.sort((a, b) => b.mrr_minor - a.mrr_minor);
    case "id_asc":
      return copy.sort((a, b) => a.organization_id.localeCompare(b.organization_id));
    case "updated_desc":
      return copy.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    default:
      return copy;
  }
}

const NO_ROWS: OrgStateOut[] = [];

export function AccountsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [sort, setSort] = useState<SortKey>("mrr_desc");

  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);

  const accounts = useAsync(() => listOrganizations(), []);

  const rows = useMemo(() => {
    const all = accounts.status === "success" ? accounts.data.organizations : NO_ROWS;
    const q = search.trim().toLowerCase();
    const filtered = all.filter((o) => {
      const matchQuery =
        q === "" ||
        o.organization_id.toLowerCase().includes(q) ||
        (o.name?.toLowerCase().includes(q) ?? false) ||
        (o.slug?.toLowerCase().includes(q) ?? false) ||
        o.active_products.some((p) => p.includes(q));
      const matchStatus =
        status === "all" || (status === "locked" && o.locked) || (status === "trial" && o.trial_ends_at !== null);
      return matchQuery && matchStatus;
    });
    return sortRows(filtered, sort);
  }, [accounts, search, status, sort]);

  const totalMrr = useMemo(() => rows.reduce((sum, r) => sum + r.mrr_minor, 0), [rows]);

  /**
   * A phone's row: the organization and its id, access state on the right, and the figures an
   * operator scans for - MRR, trial, active products, last update. The whole card opens the
   * account, as the table row does.
   */
  function accountCard(row: OrgStateOut, wiring: MobileCardWiring) {
    const who = orgIdentity({ name: row.name, slug: row.slug, organization_id: row.organization_id });
    return (
      <MobileDataCard
        {...wiring}
        onOpen={() => navigate(`/accounts/${row.organization_id}`)}
        title={who.primary}
        subtitle={<span className="mono">{who.secondary ?? row.organization_id}</span>}
        status={
          row.locked ? (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-danger">
              <LockIcon className="size-3.5" /> Locked
            </span>
          ) : (
            <span className="text-xs text-muted">Unlocked</span>
          )
        }
        fields={[
          {
            key: "mrr",
            label: "MRR",
            value:
              row.mrr_minor > 0 ? (
                <Money minor={row.mrr_minor} currency={row.currency ?? "INR"} className="font-semibold" />
              ) : (
                <span className="text-faint">—</span>
              ),
          },
          {
            key: "trial",
            label: "Trial",
            value: row.trial_ends_at ? (
              <span className="font-semibold text-late">Ends {relativeDays(row.trial_ends_at)}</span>
            ) : (
              <span className="text-faint">—</span>
            ),
          },
          {
            key: "products",
            label: "Active products",
            value:
              row.active_products.length > 0 ? (
                <span className="flex flex-wrap gap-1">
                  {row.active_products.map((p) => (
                    <Tag key={p}>{p}</Tag>
                  ))}
                </span>
              ) : (
                <span className="text-faint">—</span>
              ),
          },
          { key: "updated", label: "Last updated", value: formatDate(row.updated_at) },
        ]}
      />
    );
  }

  const columns: Column<OrgStateOut>[] = [
    {
      key: "org",
      header: "Organization",
      render: (row) => (
        <Link
          to={`/accounts/${row.organization_id}`}
          className="block truncate hover:text-accent transition-colors"
          onClick={(e) => e.stopPropagation()}
        >
          <OrgIdentity name={row.name} slug={row.slug} organizationId={row.organization_id} />
        </Link>
      ),
    },
    {
      key: "products",
      header: "Active products",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (row) =>
        row.active_products.length > 0 ? (
          <span className="flex flex-wrap gap-1">
            {row.active_products.map((p) => (
              <Tag key={p}>{p}</Tag>
            ))}
          </span>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "trial",
      header: "Trial",
      render: (row) =>
        row.trial_ends_at ? (
          <span className="text-sm font-semibold text-late">Ends {relativeDays(row.trial_ends_at)}</span>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "locked",
      header: "Access",
      render: (row) =>
        row.locked ? (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-danger">
            <LockIcon className="size-3.5" /> Locked
          </span>
        ) : (
          <span className="text-sm text-muted">Unlocked</span>
        ),
    },
    {
      key: "mrr",
      header: "MRR",
      align: "right",
      render: (row) =>
        row.mrr_minor > 0 ? (
          <Money minor={row.mrr_minor} currency={row.currency ?? "INR"} className="font-semibold" />
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "updated",
      header: "Last updated",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (row) => <span className="tnum text-muted">{formatDate(row.updated_at)}</span>,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Cross-tenant"
        title="Accounts"
        description="Every customer organization's commercial state and revenue. Select a row to drill in."
        actions={<EnvBadge />}
      />

      <p className="text-xs text-faint">
        An organization shows its account-service name and slug once folded — a row still
        showing its bare id predates that fold. Open it for the directory identity either way.
      </p>

      {/* Controls */}
      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search accounts"
            placeholder="Search by name, slug, organization id or product…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <div role="group" aria-label="Filter by status" className="-mx-1 -my-1 flex items-center gap-1.5 overflow-x-auto px-1 py-1">
            {STATUS_FILTERS.map((f) => (
              <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
                {f.label}
              </Pill>
            ))}
          </div>
          <label className="ml-auto inline-flex h-11 shrink-0 items-center gap-2 rounded-full bg-ground px-4 focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">Sort</span>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
              className="cursor-pointer bg-transparent text-sm font-semibold text-ink focus:outline-none"
            >
              {SORTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </Card>

      {/* Table */}
      <Card className="flex flex-col gap-2 p-4 sm:p-5">
        {accounts.status === "success" ? (
          <div className="flex flex-wrap items-center justify-between gap-2 px-1 pb-2">
            <span className="text-sm text-muted">
              <span className="tnum font-semibold text-ink">{rows.length}</span>{" "}
              {rows.length === 1 ? "account" : "accounts"}
            </span>
            <span className="text-sm text-muted">
              Combined MRR{" "}
              <Money minor={totalMrr} currency="INR" className="font-semibold text-ink" />
            </span>
          </div>
        ) : null}

        {accounts.status === "loading" ? (
          <div className="flex flex-col gap-2 py-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : null}

        {accounts.status === "error" ? <ErrorState message={accounts.error.message} /> : null}

        {accounts.status === "success" ? (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(row) => row.organization_id}
            caption="Customer organizations across all tenants"
            onRowClick={(row) => navigate(`/accounts/${row.organization_id}`)}
            mobileCard={accountCard}
            emptyState={
              <EmptyState
                icon={<BuildingsIcon className="size-6" />}
                title="No matching accounts"
                description="No organization matches these filters. Try a different status or adjust the search."
              />
            }
          />
        ) : null}
      </Card>
    </div>
  );
}
