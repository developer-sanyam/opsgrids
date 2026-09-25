import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { getOverview, listInventory, listPurchaseOrders, listSuppliers } from "@/api/client";
import type { InventoryItemOut, OverviewOut, PurchaseOrderOut } from "@/api/types";
import { Can, problemMessage, useAsync } from "@opsgrids/client";
import { isOutOfStock, poTotalMinor } from "@/data/derive";
import { formatDate, formatMoney, formatMoneyCompact } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import {
  BarList,
  Button,
  Card,
  CardSkeleton,
  ChartCard,
  DataTable,
  DonutChart,
  EmptyState,
  ErrorState,
  LineChart,
  MetricCard,
  NeedsAttention,
  PageHeader,
  PoStatusPill,
  SectionHeader,
  Skeleton,
  StockPill,
} from "@/ui";
import type { Column, KpiTone } from "@/ui";
import {
  AlertOctagonIcon,
  AlertTriangleIcon,
  BuildingIcon,
  ClipboardListIcon,
  CoinsIcon,
  LayersIcon,
  PlusIcon,
  TruckIcon,
} from "@opsgrids/ui";

const COUNT = new Intl.NumberFormat("en-IN");

/** One KPI tile's own async slice — its own loading/error/value, so a failure in one count (say,
 *  the active-suppliers total) never blanks the five rollups that came back fine on the same
 *  page. Mirrors the Super Admin dashboard's `KpiRow`. */
interface KpiConfig {
  key: string;
  label: string;
  icon: ReactNode;
  tone: KpiTone;
  status: "loading" | "error" | "success";
  value?: string;
  onRetry: () => void;
}

function KpiTile({ kpi }: { kpi: KpiConfig }) {
  if (kpi.status === "loading") {
    return <CardSkeleton lines={1} label={`Loading ${kpi.label.toLowerCase()}`} />;
  }
  if (kpi.status === "error") {
    return (
      <Card className="flex flex-col justify-center gap-3 p-4 sm:p-6">
        <p className="text-sm font-semibold text-ink">{`Couldn't load ${kpi.label.toLowerCase()}.`}</p>
        <Button variant="secondary" size="sm" onClick={kpi.onRetry}>
          Try again
        </Button>
      </Card>
    );
  }
  return <MetricCard label={kpi.label} value={kpi.value ?? "—"} icon={kpi.icon} tone={kpi.tone} />;
}

/** A compact recent-purchase-orders table: the newest orders, each linking to its detail. */
function recentOrderColumns(): Column<PurchaseOrderOut>[] {
  return [
    {
      key: "po_number",
      header: "PO Number",
      mobile: "title",
      cellClassName: "whitespace-nowrap",
      render: (po) => <span className="font-semibold text-ink tnum">{po.po_number}</span>,
    },
    { key: "supplier", header: "Supplier", mobile: "field", render: (po) => po.supplier_name },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      mobile: "field",
      render: (po) => (
        <span className="tnum font-semibold text-ink">{formatMoney(poTotalMinor(po), po.currency)}</span>
      ),
    },
    { key: "status", header: "Status", mobile: "status", render: (po) => <PoStatusPill status={po.status} /> },
    {
      key: "expected",
      header: "Expected",
      align: "right",
      mobile: "field",
      cellClassName: "whitespace-nowrap",
      render: (po) => formatDate(po.expected_date),
    },
  ];
}

function lowStockColumns(): Column<InventoryItemOut>[] {
  return [
    {
      key: "item",
      header: "Item",
      mobile: "title",
      render: (it) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{it.name}</span>
          <span className="text-xs text-muted tnum">{it.sku}</span>
        </div>
      ),
    },
    { key: "warehouse", header: "Warehouse", mobile: "field", render: (it) => it.warehouse },
    {
      key: "on_hand",
      header: "On hand",
      align: "right",
      mobile: "field",
      render: (it) => (
        <span className="tnum font-semibold text-ink whitespace-nowrap">
          {formatQty(it.on_hand_qty)} <span className="text-xs font-normal text-muted">{it.uom}</span>
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      mobile: "status",
      render: (it) => <StockPill level={isOutOfStock(it) ? "out" : "low"} />,
    },
  ];
}

/** Purchase orders grouped by status, as donut slices — no explicit tone per slice, so the chart
 *  assigns each present status the next colour in its own rotation, deterministic since the
 *  server always returns them in lifecycle order. */
function statusDonutSlices(overview: OverviewOut) {
  return overview.po_status_mix.map((slice) => ({ key: slice.key, label: slice.label, value: slice.count }));
}

export function OverviewPage() {
  const navigate = useNavigate();

  const overview = useAsync(() => getOverview(), []);
  const productsCount = useAsync(() => listInventory({ size: 1 }).then((r) => r.total), []);
  const suppliersCount = useAsync(() => listSuppliers({ size: 1, status: "active" }).then((r) => r.total), []);
  const recentOrders = useAsync(() => listPurchaseOrders({ sort: "created", size: 6 }).then((r) => r.items), []);
  const lowStock = useAsync(
    () => listInventory({ low_stock: true, sort: "on_hand", size: 5 }).then((r) => r.items),
    [],
  );

  const currency = overview.status === "success" ? overview.data.currency : "INR";

  const kpis: KpiConfig[] = [
    {
      key: "products",
      label: "Products",
      icon: <LayersIcon className="size-5" />,
      tone: "info",
      status: productsCount.status,
      onRetry: productsCount.reload,
      ...(productsCount.status === "success" ? { value: COUNT.format(productsCount.data) } : {}),
    },
    {
      key: "stock_value",
      label: "Stock Value",
      icon: <CoinsIcon className="size-5" />,
      tone: "accent",
      status: overview.status,
      onRetry: overview.reload,
      ...(overview.status === "success"
        ? { value: formatMoneyCompact(overview.data.inventory_value_minor, overview.data.currency) }
        : {}),
    },
    {
      key: "low_stock",
      label: "Low Stock",
      icon: <AlertTriangleIcon className="size-5" />,
      tone: "warning",
      status: overview.status,
      onRetry: overview.reload,
      ...(overview.status === "success" ? { value: COUNT.format(overview.data.low_stock_count) } : {}),
    },
    {
      key: "out_of_stock",
      label: "Out of Stock",
      icon: <AlertOctagonIcon className="size-5" />,
      tone: "danger",
      status: overview.status,
      onRetry: overview.reload,
      ...(overview.status === "success" ? { value: COUNT.format(overview.data.out_of_stock_count) } : {}),
    },
    {
      key: "pending_pos",
      label: "Pending Purchase Orders",
      icon: <ClipboardListIcon className="size-5" />,
      tone: "cyan",
      status: overview.status,
      onRetry: overview.reload,
      ...(overview.status === "success" ? { value: COUNT.format(overview.data.open_pos) } : {}),
    },
    {
      key: "suppliers",
      label: "Suppliers",
      icon: <BuildingIcon className="size-5" />,
      tone: "positive",
      status: suppliersCount.status,
      onRetry: suppliersCount.reload,
      ...(suppliersCount.status === "success" ? { value: COUNT.format(suppliersCount.data) } : {}),
    },
    {
      key: "on_time",
      label: "On-Time Delivery",
      icon: <TruckIcon className="size-5" />,
      tone: "positive",
      status: overview.status,
      onRetry: overview.reload,
      ...(overview.status === "success"
        ? { value: overview.data.on_time_pct !== null ? `${overview.data.on_time_pct.toFixed(1)}%` : "No data yet" }
        : {}),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Operations"
        title="Overview"
        description="Stock, purchase orders and suppliers across your organization."
        actions={
          <Can permission="operations.procurement.purchase_order.write" scope="OWN">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => navigate("/purchase-orders/new")}>
              New PO
            </Button>
          </Can>
        }
      />

      <section aria-label="Key metrics" className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {kpis.map((kpi) => (
          <KpiTile key={kpi.key} kpi={kpi} />
        ))}
      </section>

      <section aria-label="Spend overview" className="grid grid-cols-1 gap-4 xl:grid-cols-4">
        <div className="xl:col-span-1">
          <ChartCard
            title="Spend by Category"
            description="Committed procurement spend"
            loading={overview.status === "loading"}
            skeleton="bar"
            error={
              overview.status === "error" ? (
                <ErrorState message={problemMessage(overview.error)} onRetry={overview.reload} />
              ) : undefined
            }
            empty={
              overview.status === "success" && overview.data.spend_by_category.length === 0 ? (
                <EmptyState title="No spend yet" description="Categories appear once a purchase order is approved." />
              ) : undefined
            }
          >
            {overview.status === "success" && overview.data.spend_by_category.length > 0 ? (
              <BarList
                label="Committed spend by category"
                items={overview.data.spend_by_category.map((c) => ({ key: c.key, label: c.name, value: c.spend_minor }))}
                formatValue={(v) => formatMoneyCompact(v, currency)}
              />
            ) : null}
          </ChartCard>
        </div>

        <div className="xl:col-span-2">
          <ChartCard
            title="Spend Over Time"
            description="Committed procurement spend by month"
            loading={overview.status === "loading"}
            error={
              overview.status === "error" ? (
                <ErrorState message={problemMessage(overview.error)} onRetry={overview.reload} />
              ) : undefined
            }
            empty={
              overview.status === "success" && overview.data.spend_trend.length === 0 ? (
                <EmptyState title="No spend history yet" description="A trend appears once a purchase order is approved." />
              ) : undefined
            }
          >
            {overview.status === "success" && overview.data.spend_trend.length > 0 ? (
              <LineChart
                label="Committed spend by month"
                categories={overview.data.spend_trend.map((p) => p.label)}
                series={[
                  {
                    key: "spend",
                    label: "Spend",
                    values: overview.data.spend_trend.map((p) => p.spend_minor),
                  },
                ]}
                formatValue={(v) => formatMoneyCompact(v, currency)}
              />
            ) : null}
          </ChartCard>
        </div>

        <div className="xl:col-span-1">
          <ChartCard
            title="PO Status"
            description="Orders by stage"
            loading={overview.status === "loading"}
            skeleton="donut"
            error={
              overview.status === "error" ? (
                <ErrorState message={problemMessage(overview.error)} onRetry={overview.reload} />
              ) : undefined
            }
            empty={
              overview.status === "success" && overview.data.po_status_mix.length === 0 ? (
                <EmptyState title="No purchase orders yet" description="Statuses appear once you raise the first one." />
              ) : undefined
            }
          >
            {overview.status === "success" && overview.data.po_status_mix.length > 0 ? (
              <DonutChart label="Purchase orders by status" slices={statusDonutSlices(overview.data)} />
            ) : null}
          </ChartCard>
        </div>
      </section>

      <section aria-label="Purchase orders and low stock" className="grid grid-cols-1 gap-4 xl:grid-cols-3 items-start">
        <Card className="flex flex-col gap-4 p-4 sm:p-6 xl:col-span-2">
          <SectionHeader
            title="Recent Purchase Orders"
            action={
              <button
                type="button"
                onClick={() => navigate("/purchase-orders")}
                className="text-sm font-semibold text-accent-ink transition-colors hover:text-accent"
              >
                View All
              </button>
            }
          />
          <DataTable
            columns={recentOrderColumns()}
            rows={recentOrders.status === "success" ? recentOrders.data : []}
            rowKey={(po) => po.id}
            loading={recentOrders.status === "loading"}
            onRowClick={(po) => navigate(`/purchase-orders/${po.id}`)}
            errorState={
              recentOrders.status === "error" ? (
                <ErrorState message={problemMessage(recentOrders.error)} onRetry={recentOrders.reload} />
              ) : undefined
            }
            emptyState={<EmptyState title="No purchase orders yet" description="Raise the first one to start procurement." />}
            mobileCard="auto"
          />
        </Card>

        <Card className="flex flex-col gap-4 p-4 sm:p-6">
          <SectionHeader
            title="Low-Stock Items"
            action={
              <button
                type="button"
                onClick={() => navigate("/inventory?low_stock=true")}
                className="text-sm font-semibold text-accent-ink transition-colors hover:text-accent"
              >
                View All
              </button>
            }
          />
          <DataTable
            columns={lowStockColumns()}
            rows={lowStock.status === "success" ? lowStock.data : []}
            rowKey={(it) => it.id}
            loading={lowStock.status === "loading"}
            onRowClick={(it) => navigate(`/inventory/${it.id}`)}
            errorState={
              lowStock.status === "error" ? (
                <ErrorState message={problemMessage(lowStock.error)} onRetry={lowStock.reload} />
              ) : undefined
            }
            emptyState={<EmptyState title="Nothing low" description="Every item is above its reorder point." />}
            mobileCard="auto"
          />
        </Card>
      </section>

      <section aria-label="Attention and suppliers" className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card className="flex flex-col gap-4 p-4 sm:p-6">
          <SectionHeader
            title="Needs Attention"
            action={
              overview.status === "success" ? (
                <span className="tnum rounded-full bg-danger-soft px-3 py-1 text-xs font-bold text-danger">
                  {overview.data.attention.length}
                </span>
              ) : undefined
            }
          />
          {overview.status === "loading" ? (
            <div className="flex flex-col gap-2" role="status" aria-busy="true">
              <span className="sr-only">Loading attention items</span>
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-tile" />
              ))}
            </div>
          ) : overview.status === "error" ? (
            <ErrorState message={problemMessage(overview.error)} onRetry={overview.reload} />
          ) : overview.data.attention.length > 0 ? (
            <NeedsAttention items={overview.data.attention.slice(0, 6)} />
          ) : (
            <EmptyState title="All clear" description="Stock is healthy and every delivery is on schedule." />
          )}
        </Card>

        <div>
          <ChartCard
            title="Top Suppliers"
            description="By lifetime spend"
            loading={overview.status === "loading"}
            error={
              overview.status === "error" ? (
                <ErrorState message={problemMessage(overview.error)} onRetry={overview.reload} />
              ) : undefined
            }
            empty={
              overview.status === "success" && overview.data.top_suppliers.length === 0 ? (
                <EmptyState title="No suppliers yet" description="Add a supplier to start tracking spend." />
              ) : undefined
            }
            action={
              overview.status === "success" && overview.data.top_suppliers.length > 0 ? (
                <button
                  type="button"
                  onClick={() => navigate("/suppliers")}
                  className="text-sm font-semibold text-accent-ink transition-colors hover:text-accent"
                >
                  View All
                </button>
              ) : undefined
            }
          >
            {overview.status === "success" && overview.data.top_suppliers.length > 0 ? (
              <BarList
                label="Suppliers by lifetime spend"
                items={overview.data.top_suppliers.map((s) => ({ key: s.id, label: s.name, value: s.spend_minor }))}
                formatValue={(v) => formatMoneyCompact(v, currency)}
              />
            ) : null}
          </ChartCard>
        </div>
      </section>
    </div>
  );
}
