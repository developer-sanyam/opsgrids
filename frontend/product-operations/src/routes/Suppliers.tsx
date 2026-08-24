import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { listSuppliers } from "@/api/client";
import type { Category, SupplierOut } from "@/api/types";
import { CATEGORY_LABEL } from "@/data/derive";
import { useAsync } from "@/lib/useAsync";
import { formatMoney } from "@/lib/format";
import {
  Avatar,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
  SupplierStatusPill,
} from "@/ui";
import type { Column } from "@/ui";
import { FactoryIcon } from "@/ui/icons";

const CATEGORY_FILTERS: { value: Category | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "raw_materials", label: "Raw materials" },
  { value: "components", label: "Components" },
  { value: "packaging", label: "Packaging" },
  { value: "mro", label: "MRO" },
  { value: "logistics", label: "Logistics" },
  { value: "services", label: "Services" },
];

type SortKey = "name" | "lead_time_days" | "on_time_pct" | "spend";
interface Sort {
  key: SortKey;
  dir: "asc" | "desc";
}

const NO_ROWS: SupplierOut[] = [];

/** Colour the on-time figure by band so a weak supplier is legible — with the number carrying it. */
function onTimeClass(pct: number): string {
  if (pct >= 95) return "text-present";
  if (pct >= 85) return "text-ink";
  return "text-late";
}

export function SuppliersPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<Category | "all">("all");
  const [sort, setSort] = useState<Sort>({ key: "spend", dir: "desc" });

  const state = useAsync(listSuppliers, []);
  const items = state.status === "success" ? state.data.items : NO_ROWS;

  const rows: SupplierOut[] = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = items.filter((s) => {
      const matchesQuery =
        q === "" ||
        s.name.toLowerCase().includes(q) ||
        s.code.toLowerCase().includes(q) ||
        s.location.toLowerCase().includes(q);
      const matchesCategory = category === "all" || s.category === category;
      return matchesQuery && matchesCategory;
    });
    const dir = sort.dir === "asc" ? 1 : -1;
    return filtered.slice().sort((a, b) => {
      switch (sort.key) {
        case "lead_time_days":
          return (a.lead_time_days - b.lead_time_days) * dir;
        case "on_time_pct":
          return (a.on_time_pct - b.on_time_pct) * dir;
        case "spend":
          return (a.spend_minor - b.spend_minor) * dir;
        case "name":
        default:
          return a.name.localeCompare(b.name) * dir;
      }
    });
  }, [items, search, category, sort]);

  function onSort(key: string) {
    setSort((prev) =>
      prev.key === key
        ? { key: prev.key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key: key as SortKey, dir: "asc" },
    );
  }

  const columns: Column<SupplierOut>[] = [
    {
      key: "name",
      header: "Supplier",
      sortKey: "name",
      render: (s) => (
        <div className="flex items-center gap-3">
          <Avatar name={s.name} size="sm" />
          <div className="flex flex-col leading-tight">
            <span className="font-semibold text-ink">{s.name}</span>
            <span className="text-xs text-faint tnum">{s.code}</span>
          </div>
        </div>
      ),
    },
    {
      key: "category",
      header: "Category",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (s) => <span className="text-ink-soft">{CATEGORY_LABEL[s.category]}</span>,
    },
    {
      key: "lead",
      header: "Lead time",
      align: "right",
      sortKey: "lead_time_days",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (s) => (
        <span className="tnum text-muted whitespace-nowrap">{s.lead_time_days} days</span>
      ),
    },
    {
      key: "on_time",
      header: "On-time",
      align: "right",
      sortKey: "on_time_pct",
      render: (s) => (
        <span className={`tnum font-semibold ${onTimeClass(s.on_time_pct)}`}>
          {s.on_time_pct.toFixed(1)}%
        </span>
      ),
    },
    {
      key: "spend",
      header: "Spend",
      align: "right",
      sortKey: "spend",
      render: (s) => <span className="tnum font-semibold text-ink">{formatMoney(s.spend_minor)}</span>,
    },
    {
      key: "status",
      header: "Status",
      align: "right",
      render: (s) => (
        <div className="flex justify-end">
          <SupplierStatusPill status={s.status} />
        </div>
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Procurement"
        title="Suppliers"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <FactoryIcon className="size-4 text-muted" />
            <span className="tnum">{items.length}</span>
            <span className="text-muted font-medium">partners</span>
          </span>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <SearchInput
          label="Search suppliers"
          placeholder="Search by name, code or location…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          containerClassName="lg:max-w-sm"
        />
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 border-t border-line pt-4">
          <span className="pr-1 text-xs font-semibold uppercase tracking-wide text-faint shrink-0">
            Category
          </span>
          {CATEGORY_FILTERS.map((f) => (
            <Pill key={f.value} active={category === f.value} onClick={() => setCategory(f.value)}>
              {f.label}
            </Pill>
          ))}
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : state.status === "error" ? (
          <ErrorState message={state.error.message} />
        ) : (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(s) => s.id}
            caption="Suppliers"
            sort={sort}
            onSort={onSort}
            onRowClick={(s) => navigate(`/suppliers/${s.id}`)}
            emptyState={
              <EmptyState
                icon={<FactoryIcon className="size-6" />}
                title="No matching suppliers"
                description="No partners match these filters. Try a different category or clear the search."
              />
            }
          />
        )}
      </Card>
    </div>
  );
}
