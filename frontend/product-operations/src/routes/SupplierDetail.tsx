import { useCallback, useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getSupplier, listPurchaseOrders } from "@/api/client";
import type { PurchaseOrderOut } from "@/api/types";
import { CATEGORY_LABEL, poTotalMinor } from "@/data/derive";
import { useAsync } from "@atmora/client";
import { formatDate, formatMoney, formatMoneyCompact } from "@/lib/format";
import {
  Avatar,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  PoStatusPill,
  SectionHeader,
  Skeleton,
  SupplierStatusPill,
} from "@/ui";
import type { Column } from "@/ui";
import {
  ArrowRightIcon,
  ClipboardListIcon,
  ClockIcon,
  CoinsIcon,
  MailIcon,
  MapPinIcon,
  TruckIcon,
} from "@atmora/ui";

interface StatTileProps {
  icon: React.ReactNode;
  label: string;
  value: string;
}

function StatTile({ icon, label, value }: StatTileProps) {
  return (
    <div className="flex items-center gap-3 rounded-tile bg-ground px-4 py-3">
      <span className="grid size-9 shrink-0 place-items-center rounded-tile bg-surface text-accent-ink shadow-card">
        {icon}
      </span>
      <div className="flex min-w-0 flex-col leading-tight">
        <span className="text-xs font-medium text-muted">{label}</span>
        <span className="tnum text-lg font-extrabold text-ink">{value}</span>
      </div>
    </div>
  );
}

export function SupplierDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const supplierLoader = useCallback(() => getSupplier(id), [id]);
  const supplierState = useAsync(supplierLoader, [id]);
  const posState = useAsync(listPurchaseOrders, []);

  const supplier = supplierState.status === "success" ? supplierState.data : null;
  const supplierPos: PurchaseOrderOut[] = useMemo(() => {
    if (posState.status !== "success" || !supplier) return [];
    return posState.data.items
      .filter((po) => po.supplier_id === supplier.id)
      .sort((a, b) => b.created_date.localeCompare(a.created_date));
  }, [posState, supplier]);

  if (supplierState.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <Skeleton className="h-8 w-40 rounded-tile" />
        <Card className="p-6 flex flex-col gap-4">
          <Skeleton className="h-6 w-56" />
          <Skeleton className="h-24 w-full rounded-tile" />
        </Card>
      </div>
    );
  }

  if (supplierState.status === "error" || !supplier) {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink />
        <Card className="p-6">
          <ErrorState
            message={
              supplierState.status === "error"
                ? supplierState.error.message
                : "That supplier could not be found."
            }
            onRetry={() => navigate("/suppliers")}
          />
        </Card>
      </div>
    );
  }

  const columns: Column<PurchaseOrderOut>[] = [
    {
      key: "po_number",
      header: "PO Number",
      render: (po) => <span className="tnum font-semibold text-ink">{po.po_number}</span>,
    },
    {
      key: "created",
      header: "Raised",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (po) => <span className="tnum text-muted">{formatDate(po.created_date)}</span>,
    },
    {
      key: "total",
      header: "Total",
      align: "right",
      render: (po) => <span className="tnum font-semibold text-ink">{formatMoney(poTotalMinor(po))}</span>,
    },
    {
      key: "status",
      header: "Status",
      align: "right",
      render: (po) => (
        <div className="flex justify-end">
          <PoStatusPill status={po.status} />
        </div>
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />

      <PageHeader
        eyebrow={`${CATEGORY_LABEL[supplier.category]} · ${supplier.code}`}
        title={
          <span className="inline-flex items-center gap-3">
            {supplier.name}
            <SupplierStatusPill status={supplier.status} />
          </span>
        }
      />

      {/* Stat tiles */}
      <section aria-label="Supplier metrics" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile
          icon={<CoinsIcon className="size-5" />}
          label="Lifetime spend"
          value={formatMoneyCompact(supplier.spend_minor)}
        />
        <StatTile
          icon={<TruckIcon className="size-5" />}
          label="On-time delivery"
          value={`${supplier.on_time_pct.toFixed(1)}%`}
        />
        <StatTile
          icon={<ClockIcon className="size-5" />}
          label="Lead time"
          value={`${supplier.lead_time_days} days`}
        />
        <StatTile
          icon={<ClipboardListIcon className="size-5" />}
          label="Open POs"
          value={String(supplier.open_pos)}
        />
      </section>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3 items-start">
        {/* Purchase orders for this supplier */}
        <Card className="p-2 sm:p-4 lg:col-span-2">
          <div className="px-3 pt-2 pb-3">
            <SectionHeader
              title="Purchase Orders"
              subtitle={`${supplierPos.length} orders with this supplier`}
            />
          </div>
          {posState.status === "loading" ? (
            <div className="flex flex-col gap-3 p-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-tile" />
              ))}
            </div>
          ) : posState.status === "error" ? (
            <ErrorState message={posState.error.message} />
          ) : (
            <DataTable
              columns={columns}
              rows={supplierPos}
              rowKey={(po) => po.id}
              caption={`Purchase orders for ${supplier.name}`}
              onRowClick={(po) => navigate(`/purchase-orders/${po.id}`)}
              emptyState={
                <EmptyState
                  icon={<ClipboardListIcon className="size-6" />}
                  title="No purchase orders yet"
                  description="Orders raised against this supplier will appear here."
                />
              }
            />
          )}
        </Card>

        {/* Contact card */}
        <Card className="p-6 flex flex-col gap-4">
          <SectionHeader title="Contact" />
          <div className="flex items-center gap-3">
            <Avatar name={supplier.contact_name} size="md" />
            <div className="flex min-w-0 flex-col leading-tight">
              <span className="truncate font-bold text-ink">{supplier.contact_name}</span>
              <span className="truncate text-xs text-muted">Account manager</span>
            </div>
          </div>
          <dl className="flex flex-col gap-2.5 text-sm">
            <div className="flex items-center gap-2">
              <MailIcon className="size-4 shrink-0 text-faint" />
              <a
                href={`mailto:${supplier.contact_email}`}
                className="truncate text-accent hover:text-accent-strong transition-colors"
              >
                {supplier.contact_email}
              </a>
            </div>
            <div className="flex items-center gap-2">
              <MapPinIcon className="size-4 shrink-0 text-faint" />
              <span className="text-ink-soft">{supplier.location}</span>
            </div>
            <div className="flex items-center justify-between gap-2 border-t border-line pt-2.5">
              <dt className="text-muted">Supplier since</dt>
              <dd className="tnum font-semibold text-ink">{formatDate(supplier.since)}</dd>
            </div>
          </dl>
        </Card>
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/suppliers"
      className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to suppliers
    </Link>
  );
}
