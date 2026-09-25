import { useMemo } from "react";
import type { CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";

import { FULL_PAGE, listInventory, listPurchaseOrders, listSuppliers, TRENDS_ARE_SAMPLE } from "@/api/client";
import { ON_TIME_SAMPLE } from "@/api/fixtures";
import type { InventoryItemOut, PurchaseOrderOut, SupplierOut } from "@/api/types";
import { Can, useAuth } from "@opsgrids/client";
import {
  attentionItems,
  inventoryValueMinor,
  lowStockItems,
  openPoCount,
  poStatusMix,
  poTotalMinor,
  spendByCategory,
  weightedOnTimePct,
} from "@/data/derive";
import { useAsync } from "@opsgrids/client";
import { today } from "@/lib/now";
import { formatMoneyCompact, formatNumber } from "@/lib/format";
import {
  Button,
  Card,
  CategoryBreakdown,
  ErrorState,
  KpiCard,
  NeedsAttention,
  PoStatusPill,
  SampleChip,
  SectionHeader,
  Skeleton,
  SpendTrend,
  StatusMix,
  TopSuppliers,
} from "@/ui";
import type { KpiTone, MiniStat } from "@/ui";
import { AlertTriangleIcon, ClipboardListIcon, CoinsIcon, PlusIcon, TruckIcon } from "@opsgrids/ui";
import { SAMPLE_DATA, SAMPLE_TREND } from "@/ui/sampleCopy";

const EMPTY_POS: PurchaseOrderOut[] = [];
const EMPTY_INVENTORY: InventoryItemOut[] = [];
const EMPTY_SUPPLIERS: SupplierOut[] = [];

/** The Overview's top bar: breadcrumb + title, then New PO. The shell's own header already has a
 *  search that reaches Purchase Orders (`AppShell`'s `search.fallback`), so this doesn't repeat
 *  it. */
function OverviewHeader({ orgName }: { orgName: string }) {
  const navigate = useNavigate();

  return (
    <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
      <div className="flex flex-col gap-1">
        <nav aria-label="Breadcrumb" className="text-sm font-medium text-muted">
          Workspace <span className="text-faint">/</span>{" "}
          <span className="text-ink-soft">{orgName}</span>
        </nav>
        <h1 className="display text-3xl font-extrabold leading-tight sm:text-[2.1rem]">Overview</h1>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <Can permission="operations.procurement.purchase_order.write" scope="OWN">
          <Button
            size="md"
            leadingIcon={<PlusIcon className="size-4" />}
            onClick={() => navigate("/purchase-orders/new")}
          >
            New PO
          </Button>
        </Can>
      </div>
    </header>
  );
}

interface KpiConfig {
  key: string;
  label: string;
  value: string;
  deltaPct: number;
  goodWhen?: "down";
  series: number[];
  icon: React.ReactNode;
  tone: KpiTone;
}

function buildKpis(
  pos: PurchaseOrderOut[],
  inventory: InventoryItemOut[],
  suppliers: SupplierOut[],
): KpiConfig[] {
  const open = openPoCount(pos);
  const invValue = inventoryValueMinor(inventory);
  const low = lowStockItems(inventory).length;
  const onTime = weightedOnTimePct(suppliers);
  return [
    {
      key: "open_pos",
      label: "Open POs",
      value: formatNumber(open),
      deltaPct: 3.1,
      series: [5, 6, 6, 7, 6, 7, open],
      icon: <ClipboardListIcon className="size-5" />,
      tone: "accent",
    },
    {
      key: "inventory_value",
      label: "Inventory value",
      value: formatMoneyCompact(invValue),
      deltaPct: 2.4,
      series: [82, 84, 83, 88, 90, 92, 96],
      icon: <CoinsIcon className="size-5" />,
      tone: "info",
    },
    {
      key: "low_stock",
      label: "Low-stock items",
      value: formatNumber(low),
      deltaPct: -1.8,
      goodWhen: "down",
      series: [9, 8, 8, 7, 6, 6, low],
      icon: <AlertTriangleIcon className="size-5" />,
      tone: "warning",
    },
    {
      key: "on_time",
      label: "On-time delivery",
      value: `${onTime.toFixed(1)}%`,
      deltaPct: ON_TIME_SAMPLE.rateDelta,
      series: ON_TIME_SAMPLE.series,
      icon: <TruckIcon className="size-5" />,
      tone: "positive",
    },
  ];
}

/** A compact recent-purchase-orders panel: the newest orders, each linking to its detail. */
function RecentOrders({ pos }: { pos: PurchaseOrderOut[] }) {
  const navigate = useNavigate();
  const recent = useMemo(
    () =>
      pos
        .slice()
        .sort((a, b) => b.created_date.localeCompare(a.created_date))
        .slice(0, 6),
    [pos],
  );
  return (
    <ul className="flex flex-col">
      {recent.map((po) => (
        <li key={po.id}>
          <button
            type="button"
            onClick={() => navigate(`/purchase-orders/${po.id}`)}
            className="flex w-full items-center gap-3 rounded-tile px-2 py-2.5 text-left hover:bg-ground transition-colors"
          >
            <span className="grid size-9 shrink-0 place-items-center rounded-tile bg-accent-soft text-accent-ink">
              <ClipboardListIcon className="size-[18px]" />
            </span>
            <div className="flex min-w-0 flex-1 flex-col leading-tight">
              <span className="truncate text-sm font-semibold text-ink tnum">{po.po_number}</span>
              <span className="truncate text-xs text-muted">{po.supplier_name}</span>
            </div>
            <span className="hidden shrink-0 tnum text-sm font-semibold text-ink sm:block">
              {formatMoneyCompact(poTotalMinor(po))}
            </span>
            <PoStatusPill status={po.status} />
          </button>
        </li>
      ))}
    </ul>
  );
}

export function OverviewPage() {
  const { user } = useAuth();
  const posState = useAsync(() => listPurchaseOrders({ size: FULL_PAGE }), []);
  const inventoryState = useAsync(() => listInventory({ size: FULL_PAGE }), []);
  const suppliersState = useAsync(() => listSuppliers({ size: FULL_PAGE }), []);

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "OpsGrids";

  const pos = posState.status === "success" ? posState.data.items : EMPTY_POS;
  const inventory =
    inventoryState.status === "success" ? inventoryState.data.items : EMPTY_INVENTORY;
  const suppliers =
    suppliersState.status === "success" ? suppliersState.data.items : EMPTY_SUPPLIERS;

  const ready =
    posState.status === "success" &&
    inventoryState.status === "success" &&
    suppliersState.status === "success";
  const failed =
    posState.status === "error" ||
    inventoryState.status === "error" ||
    suppliersState.status === "error";
  const firstError =
    (posState.status === "error" && posState.error) ||
    (inventoryState.status === "error" && inventoryState.error) ||
    (suppliersState.status === "error" && suppliersState.error) ||
    null;

  function retryAll() {
    posState.reload();
    inventoryState.reload();
    suppliersState.reload();
  }

  const kpis = useMemo(
    () => buildKpis(pos, inventory, suppliers),
    [pos, inventory, suppliers],
  );
  const categories = useMemo(() => spendByCategory(pos), [pos]);
  const statusMix = useMemo(() => poStatusMix(pos), [pos]);
  const attention = useMemo(
    () => attentionItems(pos, inventory, today()),
    [pos, inventory],
  );
  const topSuppliers = useMemo(
    () => suppliers.slice().sort((a, b) => b.spend_minor - a.spend_minor).slice(0, 5),
    [suppliers],
  );

  const miniStats: MiniStat[] = useMemo(() => {
    const warehouses = new Set(inventory.map((i) => i.warehouse)).size;
    const activeSuppliers = suppliers.filter((s) => s.status === "active").length;
    const skuCount = inventory.length;
    return [
      { label: "Active suppliers", metric: "partners", total: String(activeSuppliers) },
      { label: "Warehouses", metric: "sites", total: String(warehouses) },
      { label: "Tracked SKUs", metric: "items", total: String(skuCount) },
    ];
  }, [inventory, suppliers]);

  return (
    <div className="rise flex flex-col gap-5">
      <OverviewHeader orgName={orgName} />

      {/* Row 1 — KPI cards */}
      <section aria-label="Key metrics" className="cascade grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {!ready && !failed
          ? Array.from({ length: 4 }).map((_, i) => (
              <Card key={i} className="flex flex-col gap-4 p-5" style={{ "--i": i } as CSSProperties}>
                <div className="flex justify-between">
                  <Skeleton className="size-10 rounded-tile" />
                  <Skeleton className="h-8 w-20" />
                </div>
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-9 w-24 rounded-tile" />
              </Card>
            ))
          : null}
        {failed ? (
          <Card className="p-5 sm:col-span-2 xl:col-span-4">
            <ErrorState message={firstError?.message ?? "Could not load the dashboard."} onRetry={retryAll} />
          </Card>
        ) : null}
        {ready
          ? kpis.map((kpi, i) => (
              <div key={kpi.key} style={{ "--i": i } as CSSProperties}>
                <KpiCard
                  label={kpi.label}
                  value={kpi.value}
                  deltaPct={kpi.deltaPct}
                  {...(kpi.goodWhen ? { goodWhen: kpi.goodWhen } : {})}
                  series={kpi.series}
                  icon={kpi.icon}
                  tone={kpi.tone}
                  sample={TRENDS_ARE_SAMPLE ? SAMPLE_TREND : false}
                />
              </div>
            ))
          : null}
      </section>

      {/* Row 2 — category bars · spend trend · status mix */}
      <section aria-label="Spend overview" className="grid grid-cols-1 gap-5 xl:grid-cols-4">
        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="Spend by Category" subtitle="Where the money goes" />
          {ready && categories.length > 0 ? (
            <CategoryBreakdown data={categories} />
          ) : failed ? (
            <ErrorState message={firstError?.message ?? "Failed to load."} onRetry={retryAll} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-4 p-6 xl:col-span-2">
          <SectionHeader
            title="Spend Over Time"
            subtitle="Committed procurement spend"
            {...(TRENDS_ARE_SAMPLE ? { action: <SampleChip {...SAMPLE_DATA} /> } : {})}
          />
          {ready ? <SpendTrend /> : <Skeleton className="h-56 w-full rounded-tile" />}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="PO Status" subtitle="Orders by stage" />
          {ready && statusMix.length > 0 ? (
            <StatusMix slices={statusMix} total={pos.length} stats={miniStats} />
          ) : failed ? (
            <ErrorState message={firstError?.message ?? "Failed to load."} onRetry={retryAll} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>
      </section>

      {/* Row 3 — recent orders · needs attention + top suppliers */}
      <section aria-label="Operations detail" className="grid grid-cols-1 gap-5 xl:grid-cols-3 items-start">
        <Card className="flex flex-col gap-4 p-6 xl:col-span-2">
          <SectionHeader
            title="Recent Purchase Orders"
            subtitle={ready ? `${pos.length} orders in the pipeline` : "Loading…"}
            action={
              <Link
                to="/purchase-orders"
                className="text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
              >
                View all
              </Link>
            }
          />
          {!ready && !failed ? (
            <div className="flex flex-col gap-3 py-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {failed ? <ErrorState message={firstError?.message ?? "Failed to load."} onRetry={retryAll} /> : null}
          {ready ? <RecentOrders pos={pos} /> : null}
        </Card>

        <div className="flex flex-col gap-5">
          <Card className="flex flex-col gap-4 p-6">
            <SectionHeader
              title="Needs Attention"
              action={
                <span className="tnum rounded-full bg-danger-soft px-2.5 py-1 text-xs font-bold text-danger">
                  {ready ? attention.length : 0}
                </span>
              }
            />
            {ready ? (
              attention.length > 0 ? (
                <NeedsAttention items={attention.slice(0, 6)} />
              ) : (
                <p className="py-6 text-center text-sm text-muted">
                  Nothing needs attention — stock is healthy and every delivery is on schedule.
                </p>
              )
            ) : (
              <div className="flex flex-col gap-2">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full rounded-tile" />
                ))}
              </div>
            )}
          </Card>

          <Card className="flex flex-col gap-4 p-6">
            <SectionHeader
              title="Top Suppliers"
              subtitle="By lifetime spend"
              action={
                <Link
                  to="/suppliers"
                  className="text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
                >
                  View all
                </Link>
              }
            />
            {ready ? (
              <TopSuppliers suppliers={topSuppliers} />
            ) : (
              <div className="flex flex-col gap-2">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-11 w-full rounded-tile" />
                ))}
              </div>
            )}
          </Card>
        </div>
      </section>
    </div>
  );
}
