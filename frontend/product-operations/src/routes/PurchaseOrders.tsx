import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { listPurchaseOrders } from "@/api/client";
import type { PoStatus, PurchaseOrderOut } from "@/api/types";
import { CATEGORY_LABEL, poItemCount, poTotalMinor } from "@/data/derive";
import { Can, problemMessage, useAsync } from "@opsgrids/client";
import { formatDate, formatMoney } from "@/lib/format";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  Pill,
  PoStatusPill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { ClipboardListIcon, PlusIcon, useDebouncedValue } from "@opsgrids/ui";

type StatusFilter = PoStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "draft", label: "Draft" },
  { value: "approved", label: "Approved" },
  { value: "partially_received", label: "Partially received" },
  { value: "received", label: "Received" },
  { value: "closed", label: "Closed" },
  { value: "cancelled", label: "Cancelled" },
];

type SortKey = "number" | "expected" | "created";

export function PurchaseOrdersPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const debouncedSearch = useDebouncedValue(search);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [sort, setSort] = useState<SortKey>("expected");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);

  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);

  const loader = () => listPurchaseOrders({ q: debouncedSearch, status, sort, page, size });
  const state = useAsync(loader, [debouncedSearch, status, sort, page, size]);

  const columns: Column<PurchaseOrderOut>[] = [
    {
      key: "po_number",
      header: "PO Number",
      sortKey: "number",
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
      render: (po) => <span className="text-ink-soft">{po.supplier_name}</span>,
    },
    {
      key: "items",
      header: "Items",
      align: "right",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (po) => <span className="tnum text-muted">{poItemCount(po)}</span>,
    },
    {
      key: "total",
      header: "Total",
      align: "right",
      render: (po) => (
        <span className="tnum font-semibold text-ink">
          {formatMoney(poTotalMinor(po), po.currency)}
        </span>
      ),
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
      sortKey: "expected",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (po) => <span className="tnum text-muted">{formatDate(po.expected_date)}</span>,
    },
  ];

  const filtersActive = debouncedSearch !== "" || status !== "all";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Procurement"
        title="Purchase Orders"
        actions={
          <Can permission="operations.procurement.purchase_order.write" scope="OWN">
            <Button
              leadingIcon={<PlusIcon className="size-4" />}
              onClick={() => navigate("/purchase-orders/new")}
            >
              New PO
            </Button>
          </Can>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3">
          <SearchInput
            label="Search purchase orders"
            placeholder="Search by PO number, supplier or warehouse…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            containerClassName="lg:max-w-sm"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            {STATUS_FILTERS.map((f) => (
              <Pill
                key={f.value}
                active={status === f.value}
                onClick={() => {
                  setStatus(f.value);
                  setPage(1);
                }}
              >
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
        ) : null}
        {state.status === "error" ? (
          <ErrorState
            title="Couldn't load purchase orders"
            message={problemMessage(state.error)}
            onRetry={state.reload}
          />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(po) => po.id}
              caption="Purchase orders"
              sort={{ key: sort, dir: "asc" }}
              onSort={(key) => setSort(key as SortKey)}
              onRowClick={(po) => navigate(`/purchase-orders/${po.id}`)}
              emptyState={
                <EmptyState
                  icon={<ClipboardListIcon className="size-6" />}
                  title={filtersActive ? "No purchase orders match these filters" : "No purchase orders yet"}
                  description={
                    filtersActive
                      ? "Try a different status, or clear the search."
                      : "Raise the first one to start procurement."
                  }
                  action={
                    filtersActive ? null : (
                      <Can permission="operations.procurement.purchase_order.write" scope="OWN">
                        <Button size="sm" onClick={() => navigate("/purchase-orders/new")}>
                          New PO
                        </Button>
                      </Can>
                    )
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="orders"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>
    </div>
  );
}
