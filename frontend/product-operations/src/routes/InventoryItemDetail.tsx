import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { deleteInventoryItem, getInventoryItem, listMovements } from "@/api/client";
import type { StockMovementKind, StockMovementOut } from "@/api/types";
import { CATEGORY_LABEL } from "@/data/derive";
import { Can, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import { formatDate, formatMoney, formatNumber } from "@/lib/format";
import { formatQty, qtyToMilli, stockValueMinor } from "@/lib/quantity";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  Pill,
  SectionHeader,
  Skeleton,
  StockPill,
  Tag,
} from "@/ui";
import type { Column, StockLevel } from "@/ui";
import { ArrowRightIcon, LayersIcon, useToast } from "@opsgrids/ui";
import { InventoryItemFormDialog } from "@/forms/InventoryItemFormDialog";
import { StockAdjustmentDialog } from "@/forms/StockAdjustmentDialog";

const KIND_FILTERS: { value: StockMovementKind | "all"; label: string }[] = [
  { value: "all", label: "All movements" },
  { value: "opening", label: "Opening" },
  { value: "receipt", label: "Receipts" },
  { value: "adjustment", label: "Adjustments" },
  { value: "count", label: "Counts" },
  { value: "relocation", label: "Relocations" },
];

const KIND_LABEL: Record<StockMovementKind, string> = {
  opening: "Opening balance",
  receipt: "Receipt",
  adjustment: "Adjustment",
  count: "Count",
  relocation: "Moved between warehouses",
  issue: "Issue",
};

function levelOf(onHandQty: string, reorderPoint: number): StockLevel {
  const onHand = qtyToMilli(onHandQty);
  if (onHand === 0) return "out";
  if (onHand <= reorderPoint * 1000) return "low";
  return "ok";
}

export function InventoryItemDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [adjusting, setAdjusting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [kind, setKind] = useState<StockMovementKind | "all">("all");
  const [page, setPage] = useState(1);

  const itemLoader = () => getInventoryItem(id);
  const itemState = useAsync(itemLoader, [id]);
  const movementsLoader = () => listMovements(id, { kind, page, size: 25 });
  const movementsState = useAsync(movementsLoader, [id, kind, page]);

  const remove = useMutation(() => deleteInventoryItem(id), {
    onSuccess: () => {
      toast.success(`${itemState.status === "success" ? itemState.data.name : "Item"} deleted.`);
      navigate("/inventory");
    },
  });

  const item = itemState.status === "success" ? itemState.data : null;

  if (itemState.status === "loading") {
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

  if (itemState.status === "error" || !item) {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink />
        <Card className="p-6">
          <ErrorState
            message={
              itemState.status === "error"
                ? problemMessage(itemState.error)
                : "That inventory item could not be found."
            }
            {...(itemState.status === "error" ? { onRetry: itemState.reload } : {})}
          />
        </Card>
      </div>
    );
  }

  const level = levelOf(item.on_hand_qty, item.reorder_point);

  const columns: Column<StockMovementOut>[] = [
    {
      key: "kind",
      header: "Movement",
      render: (m) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{KIND_LABEL[m.kind]}</span>
          {m.reason ? <span className="text-xs text-muted capitalize">{m.reason}</span> : null}
          {m.reference_type === "purchase_order" && m.reference_id ? (
            <Link
              to={`/purchase-orders/${m.reference_id}`}
              className="text-xs font-semibold text-accent hover:text-accent-strong transition-colors"
            >
              View purchase order
            </Link>
          ) : null}
        </div>
      ),
    },
    {
      key: "delta",
      header: "Change",
      align: "right",
      render: (m) => (
        <span className={`tnum font-semibold ${qtyToMilli(m.quantity) < 0 ? "text-negative" : "text-present"}`}>
          {qtyToMilli(m.quantity) > 0 ? "+" : ""}
          {formatQty(m.quantity)}
        </span>
      ),
    },
    {
      key: "after",
      header: "On hand after",
      align: "right",
      render: (m) => <span className="tnum text-ink-soft">{formatQty(m.balance_after)}</span>,
    },
    {
      key: "occurred",
      header: "Date",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (m) => <span className="tnum text-muted">{formatDate(m.occurred_on)}</span>,
    },
    {
      key: "note",
      header: "Note",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (m) => <span className="text-ink-soft">{m.note || "—"}</span>,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />

      <PageHeader
        eyebrow={`${CATEGORY_LABEL[item.category]} · ${item.sku}`}
        title={item.name}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Can permission="operations.inventory.stock.adjust" scope="ORG">
              <Button onClick={() => setAdjusting(true)}>Adjust stock</Button>
            </Can>
            <Can permission="operations.inventory.item.write" scope="ORG">
              <Button variant="outline" onClick={() => setEditing(true)}>
                Edit
              </Button>
              <Button variant="danger" onClick={() => setDeleting(true)}>
                Delete
              </Button>
            </Can>
          </div>
        }
      />

      <section aria-label="Item metrics" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <div className="flex flex-col gap-1 rounded-tile bg-ground px-4 py-3">
          <span className="text-xs font-medium text-muted">On hand</span>
          <span className="tnum text-lg font-extrabold text-ink">
            {formatQty(item.on_hand_qty)} {item.uom}
          </span>
        </div>
        <div className="flex flex-col gap-1 rounded-tile bg-ground px-4 py-3">
          <span className="text-xs font-medium text-muted">Reorder point</span>
          <span className="tnum text-lg font-extrabold text-ink">{formatNumber(item.reorder_point)}</span>
        </div>
        <div className="flex flex-col gap-1 rounded-tile bg-ground px-4 py-3">
          <span className="text-xs font-medium text-muted">Stock value</span>
          <span className="tnum text-lg font-extrabold text-ink">
            {formatMoney(stockValueMinor(item.on_hand_qty, item.unit_value_minor), item.currency)}
          </span>
        </div>
        <div className="flex flex-col gap-1.5 rounded-tile bg-ground px-4 py-3">
          <span className="text-xs font-medium text-muted">Status</span>
          <StockPill level={level} />
        </div>
      </section>

      <Card className="p-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-muted">Warehouse</span>
          <Tag>{item.warehouse}</Tag>
        </div>
        <span className="text-xs text-muted">Last updated {formatDate(item.updated_at)}</span>
      </Card>

      <Card className="p-2 sm:p-4">
        <div className="px-3 pt-2 pb-3 flex flex-col gap-3">
          <SectionHeader title="Stock Movements" subtitle="Newest first. Append-only — no row can be edited or deleted." />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            {KIND_FILTERS.map((f) => (
              <Pill
                key={f.value}
                active={kind === f.value}
                onClick={() => {
                  setKind(f.value);
                  setPage(1);
                }}
              >
                {f.label}
              </Pill>
            ))}
          </div>
        </div>
        {movementsState.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {movementsState.status === "error" ? (
          <ErrorState message={problemMessage(movementsState.error)} onRetry={movementsState.reload} />
        ) : null}
        {movementsState.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={movementsState.data.items}
              rowKey={(m) => m.id}
              caption={`Stock movements for ${item.name}`}
              emptyState={
                <EmptyState
                  icon={<LayersIcon className="size-6" />}
                  title="No stock movements yet"
                  description="Movements appear here once stock is received, adjusted or counted."
                />
              }
            />
            <Pagination
              page={movementsState.data.page}
              pages={movementsState.data.pages}
              total={movementsState.data.total}
              size={movementsState.data.size}
              noun="movements"
              onPageChange={setPage}
            />
          </>
        ) : null}
      </Card>

      {editing ? (
        <InventoryItemFormDialog
          item={item}
          onClose={() => setEditing(false)}
          onSaved={(saved) => {
            setEditing(false);
            toast.success(`${saved.name} saved.`);
            itemState.reload();
          }}
        />
      ) : null}

      {adjusting ? (
        <StockAdjustmentDialog
          item={item}
          onClose={() => setAdjusting(false)}
          onSaved={(saved) => {
            setAdjusting(false);
            toast.success(`Stock adjusted — ${formatQty(saved.on_hand_qty)} ${saved.uom} on hand.`);
            itemState.reload();
            movementsState.reload();
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
        title="Delete this item?"
        body={`${item.sku} — ${item.name} is removed from inventory. This can't be undone.`}
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
      to="/inventory"
      className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to inventory
    </Link>
  );
}
