import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { deleteSupplier, getSupplier, listPurchaseOrders, updateSupplier } from "@/api/client";
import type { PurchaseOrderOut } from "@/api/types";
import { CATEGORY_LABEL, poTotalMinor } from "@/data/derive";
import { Can, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import { formatDate, formatMoney, formatMoneyCompact } from "@/lib/format";
import {
  Avatar,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  InlineAlert,
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
  useToast,
} from "@opsgrids/ui";
import { SupplierFormDialog } from "@/forms/SupplierFormDialog";

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
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const supplierLoader = () => getSupplier(id);
  const supplierState = useAsync(supplierLoader, [id]);
  const posLoader = () => listPurchaseOrders({ supplier_id: id, size: 50, sort: "created" });
  const posState = useAsync(posLoader, [id]);

  const supplier = supplierState.status === "success" ? supplierState.data : null;
  const supplierPos: PurchaseOrderOut[] = posState.status === "success" ? posState.data.items : [];

  const setHold = useMutation(() => updateSupplier(id, { status: "on_hold" }), {
    onSuccess: () => {
      toast.success(`${supplier?.name ?? "Supplier"} put on hold.`);
      supplierState.reload();
    },
  });
  const reactivate = useMutation(() => updateSupplier(id, { status: "active" }), {
    onSuccess: () => {
      toast.success(`${supplier?.name ?? "Supplier"} reactivated.`);
      supplierState.reload();
    },
  });
  const remove = useMutation(() => deleteSupplier(id), {
    onSuccess: () => {
      toast.success(`${supplier?.name ?? "Supplier"} deleted.`);
      navigate("/suppliers");
    },
  });

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
                ? problemMessage(supplierState.error)
                : "That supplier could not be found."
            }
            {...(supplierState.status === "error" ? { onRetry: supplierState.reload } : {})}
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
        actions={
          <Can permission="operations.procurement.supplier.write" scope="ORG">
            <div className="flex flex-wrap items-center gap-2">
              {supplier.status === "on_hold" ? (
                <Button
                  variant="outline"
                  loading={reactivate.pending}
                  onClick={() => void reactivate.mutate(undefined)}
                >
                  Reactivate
                </Button>
              ) : (
                <Button
                  variant="outline"
                  loading={setHold.pending}
                  onClick={() => void setHold.mutate(undefined)}
                >
                  Put on hold
                </Button>
              )}
              <Button variant="outline" onClick={() => setEditing(true)}>
                Edit
              </Button>
              <Button variant="danger" onClick={() => setDeleting(true)}>
                Delete
              </Button>
            </div>
          </Can>
        }
      />

      {setHold.error ? <InlineAlert tone="danger" title={problemMessage(setHold.error)} /> : null}
      {reactivate.error ? (
        <InlineAlert tone="danger" title={problemMessage(reactivate.error)} />
      ) : null}

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
              {...(posState.status === "success"
                ? {
                    subtitle: `${posState.data.total} order${posState.data.total === 1 ? "" : "s"} with this supplier`,
                  }
                : {})}
            />
          </div>
          {posState.status === "loading" ? (
            <div className="flex flex-col gap-3 p-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-tile" />
              ))}
            </div>
          ) : posState.status === "error" ? (
            <ErrorState message={problemMessage(posState.error)} onRetry={posState.reload} />
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
            <Avatar name={supplier.contact_name || supplier.name} size="md" />
            <div className="flex min-w-0 flex-col leading-tight">
              <span className="truncate font-bold text-ink">
                {supplier.contact_name || "No contact on file"}
              </span>
              <span className="truncate text-xs text-muted">Account manager</span>
            </div>
          </div>
          <dl className="flex flex-col gap-2.5 text-sm">
            {supplier.contact_email ? (
              <div className="flex items-center gap-2">
                <MailIcon className="size-4 shrink-0 text-faint" />
                <a
                  href={`mailto:${supplier.contact_email}`}
                  className="truncate text-accent hover:text-accent-strong transition-colors"
                >
                  {supplier.contact_email}
                </a>
              </div>
            ) : null}
            {supplier.location ? (
              <div className="flex items-center gap-2">
                <MapPinIcon className="size-4 shrink-0 text-faint" />
                <span className="text-ink-soft">{supplier.location}</span>
              </div>
            ) : null}
            <div className="flex items-center justify-between gap-2 border-t border-line pt-2.5">
              <dt className="text-muted">Supplier since</dt>
              <dd className="tnum font-semibold text-ink">{formatDate(supplier.since)}</dd>
            </div>
          </dl>
        </Card>
      </div>

      {editing ? (
        <SupplierFormDialog
          supplier={supplier}
          onClose={() => setEditing(false)}
          onSaved={(saved) => {
            setEditing(false);
            toast.success(`${saved.name} saved.`);
            supplierState.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting}
        onClose={() => {
          setDeleting(false);
          remove.reset();
        }}
        onConfirm={() => void remove.mutate(undefined)}
        title="Delete this supplier?"
        body={`${supplier.name} is removed from the supplier directory. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/suppliers"
      className="relative hit-44 inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to suppliers
    </Link>
  );
}
