import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { listPurchaseOrders } from "@/api/client";
import type { PoStatus, PurchaseOrderOut } from "@/api/types";
import { CATEGORY_LABEL, poItemCount, poTotalMinor } from "@/data/derive";
import { useAsync } from "@opsgrids/client";
import { formatDate, formatMoney } from "@/lib/format";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  PoStatusPill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { NewPurchaseOrderDialog } from "@/ui/NewPurchaseOrderDialog";
import { Announcer, ClipboardListIcon, PlusIcon } from "@opsgrids/ui";

type StatusFilter = PoStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "draft", label: "Draft" },
  { value: "approved", label: "Approved" },
  { value: "received", label: "Received" },
  { value: "closed", label: "Closed" },
];

type SortKey = "po_number" | "supplier_name" | "total" | "expected_date";
interface Sort {
  key: SortKey;
  dir: "asc" | "desc";
}

const NO_ROWS: PurchaseOrderOut[] = [];

/** A PO decorated with its derived total, so sorting and rendering share one number. */
interface Row extends PurchaseOrderOut {
  _total: number;
  _items: number;
}

function PurchaseOrdersScreen() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [sort, setSort] = useState<Sort>({ key: "expected_date", dir: "asc" });
  const [creating, setCreating] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);

  const state = useAsync(listPurchaseOrders, [attempt]);
  const items = state.status === "success" ? state.data.items : NO_ROWS;

  function handleCreated() {
    setCreating(false);
    setAttempt((current) => current + 1);
  }

  const rows: Row[] = useMemo(() => {
    const q = search.trim().toLowerCase();
    const enriched = items
      .filter((po) => {
        const matchesQuery =
          q === "" ||
          po.po_number.toLowerCase().includes(q) ||
          po.supplier_name.toLowerCase().includes(q) ||
          CATEGORY_LABEL[po.category].toLowerCase().includes(q);
        const matchesStatus = status === "all" || po.status === status;
        return matchesQuery && matchesStatus;
      })
      .map((po) => ({ ...po, _total: poTotalMinor(po), _items: poItemCount(po) }));

    const dir = sort.dir === "asc" ? 1 : -1;
    enriched.sort((a, b) => {
      switch (sort.key) {
        case "total":
          return (a._total - b._total) * dir;
        case "supplier_name":
          return a.supplier_name.localeCompare(b.supplier_name) * dir;
        case "expected_date":
          return a.expected_date.localeCompare(b.expected_date) * dir;
        case "po_number":
        default:
          return a.po_number.localeCompare(b.po_number) * dir;
      }
    });
    return enriched;
  }, [items, search, status, sort]);

  function onSort(key: string) {
    setSort((prev) =>
      prev.key === key
        ? { key: prev.key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key: key as SortKey, dir: "asc" },
    );
  }

  const columns: Column<Row>[] = [
    {
      key: "po_number",
      header: "PO Number",
      sortKey: "po_number",
      render: (po) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink tnum">{po.po_number}</span>
          <span className="text-xs text-muted">{CATEGORY_LABEL[po.category]}</span>
        </div>
      ),
    },
    {
      key: "supplier",
      header: "Supplier",
      sortKey: "supplier_name",
      render: (po) => <span className="text-ink-soft">{po.supplier_name}</span>,
    },
    {
      key: "items",
      header: "Items",
      align: "right",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (po) => <span className="tnum text-muted">{po._items}</span>,
    },
    {
      key: "total",
      header: "Total",
      align: "right",
      sortKey: "total",
      render: (po) => <span className="tnum font-semibold text-ink">{formatMoney(po._total)}</span>,
    },
    {
      key: "status",
      header: "Status",
      render: (po) => <PoStatusPill status={po.status} />,
    },
    {
      key: "expected",
      header: "Expected",
      align: "right",
      sortKey: "expected_date",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (po) => <span className="tnum text-muted">{formatDate(po.expected_date)}</span>,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Procurement"
        title="Purchase Orders"
        actions={
          <>
            <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
              <ClipboardListIcon className="size-4 text-muted" />
              <span className="tnum">{items.length}</span>
              <span className="text-muted font-medium">orders</span>
            </span>
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
              New purchase order
            </Button>
          </>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3">
          <SearchInput
            label="Search purchase orders"
            placeholder="Search by PO number or supplier…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            {STATUS_FILTERS.map((f) => (
              <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
                {f.label}
              </Pill>
            ))}
          </div>
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
            rowKey={(po) => po.id}
            caption="Purchase orders"
            sort={sort}
            onSort={onSort}
            onRowClick={(po) => navigate(`/purchase-orders/${po.id}`)}
            emptyState={
              <EmptyState
                icon={<ClipboardListIcon className="size-6" />}
                title="No matching purchase orders"
                description="No orders match these filters. Try a different status or clear the search."
              />
            }
          />
        )}
      </Card>

      <NewPurchaseOrderDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={handleCreated}
      />
    </div>
  );
}

export function PurchaseOrdersPage() {
  return (
    <Announcer>
      <PurchaseOrdersScreen />
    </Announcer>
  );
}
