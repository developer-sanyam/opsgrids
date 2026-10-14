import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { listProductionOrders } from "@/api/client";
import type { ProductionOrderOut, ProductionOrderStatus } from "@/api/types";
import { ProductionOrderFormDialog } from "@/forms/ProductionOrderFormDialog";
import { WastageThresholdsPanel } from "@/routes/manufacturing/WastageThresholdsPanel";
import { formatDate } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { Can, problemMessage, useAsync, useCan } from "@opsgrids/client";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  Pill,
  ProductionOrderStatusPill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { BoxIcon, PlusIcon } from "@opsgrids/ui";

type StatusFilter = ProductionOrderStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "DRAFT", label: "Draft" },
  { value: "APPROVED", label: "Approved" },
  { value: "IN_PROGRESS", label: "In progress" },
  { value: "COMPLETED", label: "Completed" },
  { value: "CANCELLED", label: "Cancelled" },
];

/**
 * Production orders (inventory Phase 5). Reading is `operations.manufacturing.production_order.read`;
 * "New production order" is `...production_order.write`. The wastage thresholds sit below the list:
 * everyone who can read orders sees them, and `operations.manufacturing.bom.write` edits them.
 */
export function ProductionOrdersPage() {
  const navigate = useNavigate();
  const canEditThresholds = useCan("operations.manufacturing.bom.write", "ORG");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);

  const state = useAsync(
    () => listProductionOrders({ status, q: search, page, size }),
    [status, search, page, size],
  );

  const columns: Column<ProductionOrderOut>[] = [
    {
      key: "number",
      header: "Order",
      mobile: "title",
      render: (o) => (
        <Link to={`/manufacturing/orders/${o.id}`} className="font-semibold text-ink tnum hover:underline">
          {o.number}
        </Link>
      ),
    },
    {
      key: "item",
      header: "Item",
      render: (o) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{o.sku}</span>
          <span className="text-xs text-muted">{o.name}</span>
        </div>
      ),
    },
    { key: "status", header: "Status", render: (o) => <ProductionOrderStatusPill status={o.status} /> },
    {
      key: "planned",
      header: "Planned",
      align: "right",
      render: (o) => (
        <span className="tnum">
          {formatQty(o.planned_quantity)} {o.uom}
        </span>
      ),
    },
    {
      key: "produced",
      header: "Produced",
      align: "right",
      render: (o) => (
        <span className="tnum text-muted">
          {formatQty(o.produced_quantity)} {o.uom}
        </span>
      ),
    },
    {
      key: "due",
      header: "Due",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (o) => <span className="text-muted tnum">{formatDate(o.due_date)}</span>,
    },
    {
      key: "bom",
      header: "BOM version",
      mobile: "hidden",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (o) => (
        <span className="text-muted tnum">
          {o.bom_code} · v{o.bom_version_no}
        </span>
      ),
    },
  ];

  const filtered = status !== "all" || search !== "";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Manufacturing"
        title="Production orders"
        description="Plan production from a BOM, move material to the floor, record what comes out and what was lost."
        actions={
          <Can permission="operations.manufacturing.production_order.write" scope="ORG">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
              New production order
            </Button>
          </Can>
        }
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-6">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search production orders"
            placeholder="Search by order number…"
            value={search}
            onDebouncedChange={(q) => {
              setSearch(q);
              setPage(1);
            }}
            containerClassName="lg:max-w-sm"
          />
          <div className="-mx-1 flex items-center gap-2 overflow-x-auto px-1">
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
          <div className="flex flex-col gap-3 p-3" aria-busy="true" aria-label="Loading production orders">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState title="Couldn't load production orders" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(o) => o.id}
              caption="Production orders"
              emptyState={
                <EmptyState
                  icon={<BoxIcon className="size-6" />}
                  title={filtered ? "No production orders match" : "No production orders yet"}
                  description={
                    filtered
                      ? "Try a different status or search, or clear the filters."
                      : "Plan the first one from a published BOM."
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

      <WastageThresholdsPanel canWrite={canEditThresholds} />

      {creating ? (
        <Can permission="operations.manufacturing.production_order.write" scope="ORG">
          <ProductionOrderFormDialog
            onClose={() => setCreating(false)}
            onSaved={(order) => {
              setCreating(false);
              navigate(`/manufacturing/orders/${order.id}`);
            }}
          />
        </Can>
      ) : null}
    </div>
  );
}
