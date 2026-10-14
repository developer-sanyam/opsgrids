import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { listOrders } from "@/api/client";
import type { OrderChannel, OrderOut, OrderStatus } from "@/api/types";
import { Can, problemMessage, useAsync } from "@opsgrids/client";
import { formatDate, formatMoney } from "@/lib/format";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  OrderStatusPill,
  PageHeader,
  Pagination,
  Pill,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { ClipboardListIcon, PlusIcon } from "@opsgrids/ui";

type StatusFilter = OrderStatus | "all";
type ChannelFilter = OrderChannel | "all";

/** Every order status, in the order ADR-0025 §1 defines it — the filter row offers exactly this
 *  set, never a subset invented for this one screen. */
const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "DRAFT", label: "Draft" },
  { value: "PLACED", label: "Placed" },
  { value: "ACCEPTED", label: "Accepted" },
  { value: "IN_FULFILMENT", label: "In fulfilment" },
  { value: "ON_HOLD", label: "On hold" },
  { value: "READY", label: "Ready" },
  { value: "HANDED_OVER", label: "Handed over" },
  { value: "DELIVERED", label: "Delivered" },
  { value: "COMPLETED", label: "Completed" },
  { value: "CANCELLED", label: "Cancelled" },
  { value: "REJECTED", label: "Rejected" },
];

const CHANNEL_FILTERS: { value: ChannelFilter; label: string }[] = [
  { value: "all", label: "Every channel" },
  { value: "phone", label: "Phone" },
  { value: "manual", label: "Manual" },
  { value: "sales", label: "Sales" },
  { value: "walk_in", label: "Walk-in" },
];

const CHANNEL_LABEL: Record<OrderChannel, string> = {
  phone: "Phone",
  manual: "Manual",
  sales: "Sales",
  walk_in: "Walk-in",
};

export function OrdersPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<StatusFilter>("all");
  const [channel, setChannel] = useState<ChannelFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);

  const loader = () => listOrders({ status, channel, page, size });
  const state = useAsync(loader, [status, channel, page, size]);

  const columns: Column<OrderOut>[] = [
    {
      key: "number",
      header: "Order",
      mobile: "title",
      render: (order) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink tnum">{order.number}</span>
          <span className="text-xs text-muted">{CHANNEL_LABEL[order.channel]}</span>
        </div>
      ),
    },
    {
      key: "customer",
      header: "Customer",
      render: (order) => (
        <div className="flex flex-col leading-tight">
          <span className="text-ink-soft">{order.customer_name || "—"}</span>
          <span className="text-xs text-faint tnum">{order.customer_phone || "—"}</span>
        </div>
      ),
    },
    {
      key: "lines",
      header: "Lines",
      mobile: "hidden",
      align: "right",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (order) => <span className="tnum text-muted">{order.lines.length}</span>,
    },
    {
      key: "total",
      header: "Total",
      align: "right",
      render: (order) => (
        <span className="tnum font-semibold text-ink">{formatMoney(order.total_minor, order.currency)}</span>
      ),
    },
    {
      key: "status",
      header: "Status",
      mobile: "status",
      render: (order) => <OrderStatusPill status={order.status} />,
    },
    {
      key: "created",
      header: "Created",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (order) => <span className="tnum text-muted">{formatDate(order.created_at.slice(0, 10))}</span>,
    },
  ];

  const filtersActive = status !== "all" || channel !== "all";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Fulfilment"
        title="Orders"
        actions={
          <Can permission="operations.orders.order.create">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => navigate("/orders/new")}>
              New order
            </Button>
          </Can>
        }
      />

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3">
          <div className="flex items-center gap-2 overflow-x-auto -mx-1 px-1">
            {CHANNEL_FILTERS.map((f) => (
              <Pill
                key={f.value}
                active={channel === f.value}
                onClick={() => {
                  setChannel(f.value);
                  setPage(1);
                }}
              >
                {f.label}
              </Pill>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2 overflow-x-auto -mx-1 px-1">
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
          <ErrorState title="Couldn't load orders" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(order) => order.id}
              caption="Orders"
              onRowClick={(order) => navigate(`/orders/${order.id}`)}
              emptyState={
                <EmptyState
                  icon={<ClipboardListIcon className="size-6" />}
                  title={filtersActive ? "No orders match these filters" : "No orders yet"}
                  description={
                    filtersActive
                      ? "Try a different status or channel, or clear the filters."
                      : "Take the first call, walk-in or sales order to start fulfilment."
                  }
                  action={
                    filtersActive ? null : (
                      <Can permission="operations.orders.order.create">
                        <Button size="sm" onClick={() => navigate("/orders/new")}>
                          New order
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
