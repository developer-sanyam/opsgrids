import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import {
  deleteInventoryItem,
  deleteItemAttribute,
  deleteItemVariant,
  getInventoryItem,
  listItemAttributes,
  listItemVariants,
  listMovements,
} from "@/api/client";
import type { ItemAttributeOut, ItemVariantOut, StockMovementKind, StockMovementOut } from "@/api/types";
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
  RowActions,
  SectionHeader,
  Skeleton,
  StockPill,
  Tabs,
  Tag,
} from "@/ui";
import type { Column, StockLevel } from "@/ui";
import { ArrowRightIcon, BoxIcon, LayersIcon, PlusIcon, TagIcon, useToast } from "@opsgrids/ui";
import { InventoryItemFormDialog } from "@/forms/InventoryItemFormDialog";
import { ItemAttributeFormDialog } from "@/forms/ItemAttributeFormDialog";
import { ItemVariantFormDialog } from "@/forms/ItemVariantFormDialog";
import { OpeningStockDialog } from "@/forms/OpeningStockDialog";
import { StockAdjustmentDialog } from "@/forms/StockAdjustmentDialog";
import {
  ItemLedgerPanel,
  ItemLocationsPanel,
  ItemLotsPanel,
  ItemRollsPanel,
} from "@/tracking/ItemTabs";

const ITEM_TABS = [
  { id: "overview", label: "Overview" },
  { id: "locations", label: "Locations" },
  { id: "lots", label: "Lots" },
  { id: "rolls", label: "Rolls" },
  { id: "ledger", label: "Ledger" },
];

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

function formatAttributeValue(attribute: ItemAttributeOut): string {
  switch (attribute.value_type) {
    case "text":
      return attribute.value_text ?? "";
    case "number":
      return attribute.value_number !== null ? formatNumber(attribute.value_number) : "";
    case "boolean":
      return attribute.value_boolean ? "Yes" : "No";
    case "date":
      return attribute.value_date ? formatDate(attribute.value_date) : "";
  }
}

export function InventoryItemDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [adjusting, setAdjusting] = useState(false);
  const [openingStock, setOpeningStock] = useState(false);
  const [params, setParams] = useSearchParams();
  const requestedTab = params.get("tab") ?? "overview";
  const tab = ITEM_TABS.some((t) => t.id === requestedTab) ? requestedTab : "overview";
  const setTab = (next: string) => setParams(next === "overview" ? {} : { tab: next }, { replace: true });
  const [deleting, setDeleting] = useState(false);
  const [kind, setKind] = useState<StockMovementKind | "all">("all");
  const [page, setPage] = useState(1);
  const [addingAttribute, setAddingAttribute] = useState(false);
  const [editingAttribute, setEditingAttribute] = useState<ItemAttributeOut | null>(null);
  const [deletingAttribute, setDeletingAttribute] = useState<ItemAttributeOut | null>(null);
  const [addingVariant, setAddingVariant] = useState(false);
  const [editingVariant, setEditingVariant] = useState<ItemVariantOut | null>(null);
  const [deletingVariant, setDeletingVariant] = useState<ItemVariantOut | null>(null);

  const itemLoader = () => getInventoryItem(id);
  const itemState = useAsync(itemLoader, [id]);
  const movementsLoader = () => listMovements(id, { kind, page, size: 25 });
  const movementsState = useAsync(movementsLoader, [id, kind, page]);
  const attributesLoader = () => listItemAttributes(id);
  const attributesState = useAsync(attributesLoader, [id]);
  const variantsLoader = () => listItemVariants(id);
  const variantsState = useAsync(variantsLoader, [id]);

  const remove = useMutation(() => deleteInventoryItem(id), {
    onSuccess: () => {
      toast.success(`${itemState.status === "success" ? itemState.data.name : "Item"} deleted.`);
      navigate("/inventory");
    },
  });

  const removeAttribute = useMutation(
    (attribute: ItemAttributeOut) => deleteItemAttribute(id, attribute.key),
    {
      onSuccess: (_result, attribute) => {
        setDeletingAttribute(null);
        toast.success(`${attribute.key} removed.`);
        attributesState.reload();
      },
    },
  );

  const removeVariant = useMutation((variant: ItemVariantOut) => deleteItemVariant(id, variant.id), {
    onSuccess: (_result, variant) => {
      setDeletingVariant(null);
      toast.success(`${variant.sku} removed.`);
      variantsState.reload();
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

  const attributeColumns: Column<ItemAttributeOut>[] = [
    { key: "key", header: "Key", render: (a) => <span className="font-semibold text-ink">{a.key}</span> },
    {
      key: "value",
      header: "Value",
      render: (a) => <span className="tnum text-ink-soft">{formatAttributeValue(a)}</span>,
    },
    {
      key: "type",
      header: "Type",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (a) => <Tag>{a.value_type}</Tag>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (a) => (
        <RowActions
          label={a.key}
          actions={[
            { label: "Edit", onSelect: () => setEditingAttribute(a) },
            { label: "Remove", tone: "danger", onSelect: () => setDeletingAttribute(a) },
          ]}
        />
      ),
    },
  ];

  const variantColumns: Column<ItemVariantOut>[] = [
    { key: "sku", header: "SKU", render: (v) => <span className="tnum font-semibold text-ink">{v.sku}</span> },
    { key: "name", header: "Name", render: (v) => <span className="text-ink-soft">{v.name}</span> },
    {
      key: "status",
      header: "Status",
      render: (v) => (
        <span className={v.is_active ? "text-present font-semibold" : "text-muted"}>
          {v.is_active ? "Active" : "Inactive"}
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (v) => (
        <RowActions
          label={v.sku}
          actions={[
            { label: "Edit", onSelect: () => setEditingVariant(v) },
            { label: "Remove", tone: "danger", onSelect: () => setDeletingVariant(v) },
          ]}
        />
      ),
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
              <Button variant="outline" onClick={() => setOpeningStock(true)}>
                Opening stock
              </Button>
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

      <Tabs label="Item sections" items={ITEM_TABS} value={tab} onChange={setTab} activation="manual">
        {tab === "overview" ? (
          <div className="flex flex-col gap-6">
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

          <Card className="p-2 sm:p-4">
            <div className="px-3 pt-2 pb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <SectionHeader
                title="Attributes"
                subtitle="Typed facts about this item — GSM, width, shade, composition…"
              />
              <Can permission="operations.inventory.item.write" scope="ORG">
                <Button
                  size="sm"
                  variant="outline"
                  leadingIcon={<PlusIcon className="size-4" />}
                  onClick={() => setAddingAttribute(true)}
                >
                  Add attribute
                </Button>
              </Can>
            </div>
            {attributesState.status === "loading" ? (
              <div className="flex flex-col gap-3 p-3">
                {Array.from({ length: 2 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full rounded-tile" />
                ))}
              </div>
            ) : null}
            {attributesState.status === "error" ? (
              <ErrorState message={problemMessage(attributesState.error)} onRetry={attributesState.reload} />
            ) : null}
            {attributesState.status === "success" ? (
              <DataTable
                columns={attributeColumns}
                rows={attributesState.data}
                rowKey={(a) => a.id}
                caption={`Attributes for ${item.name}`}
                emptyState={
                  <EmptyState
                    icon={<TagIcon className="size-6" />}
                    title="No attributes yet"
                    description="Record GSM, width, shade or any other fact this item's category needs."
                    action={
                      <Can permission="operations.inventory.item.write" scope="ORG">
                        <Button size="sm" onClick={() => setAddingAttribute(true)}>
                          Add attribute
                        </Button>
                      </Can>
                    }
                  />
                }
              />
            ) : null}
          </Card>

          <Card className="p-2 sm:p-4">
            <div className="px-3 pt-2 pb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <SectionHeader title="Variants" subtitle="Purchasable/stockable variants of this item." />
              <Can permission="operations.inventory.item.write" scope="ORG">
                <Button
                  size="sm"
                  variant="outline"
                  leadingIcon={<PlusIcon className="size-4" />}
                  onClick={() => setAddingVariant(true)}
                >
                  Add variant
                </Button>
              </Can>
            </div>
            {variantsState.status === "loading" ? (
              <div className="flex flex-col gap-3 p-3">
                {Array.from({ length: 2 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full rounded-tile" />
                ))}
              </div>
            ) : null}
            {variantsState.status === "error" ? (
              <ErrorState message={problemMessage(variantsState.error)} onRetry={variantsState.reload} />
            ) : null}
            {variantsState.status === "success" ? (
              <DataTable
                columns={variantColumns}
                rows={variantsState.data}
                rowKey={(v) => v.id}
                caption={`Variants of ${item.name}`}
                emptyState={
                  <EmptyState
                    icon={<BoxIcon className="size-6" />}
                    title="No variants yet"
                    description="Add a size, shade or length cut with its own SKU."
                    action={
                      <Can permission="operations.inventory.item.write" scope="ORG">
                        <Button size="sm" onClick={() => setAddingVariant(true)}>
                          Add variant
                        </Button>
                      </Can>
                    }
                  />
                }
              />
            ) : null}
          </Card>
          </div>
        ) : null}
        {tab === "locations" ? <ItemLocationsPanel item={item} onChanged={itemState.reload} /> : null}
        {tab === "lots" ? <ItemLotsPanel item={item} onChanged={itemState.reload} /> : null}
        {tab === "rolls" ? <ItemRollsPanel item={item} onChanged={itemState.reload} /> : null}
        {tab === "ledger" ? <ItemLedgerPanel item={item} onChanged={itemState.reload} /> : null}
      </Tabs>

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

      {openingStock ? (
        <OpeningStockDialog
          item={item}
          onClose={() => setOpeningStock(false)}
          onSaved={() => {
            setOpeningStock(false);
            toast.success("Opening stock posted.");
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

      {addingAttribute ? (
        <ItemAttributeFormDialog
          itemId={id}
          attribute={null}
          onClose={() => setAddingAttribute(false)}
          onSaved={(saved) => {
            setAddingAttribute(false);
            toast.success(`${saved.key} added.`);
            attributesState.reload();
          }}
        />
      ) : null}

      {editingAttribute ? (
        <ItemAttributeFormDialog
          itemId={id}
          attribute={editingAttribute}
          onClose={() => setEditingAttribute(null)}
          onSaved={(saved) => {
            setEditingAttribute(null);
            toast.success(`${saved.key} saved.`);
            attributesState.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deletingAttribute !== null}
        onClose={() => {
          setDeletingAttribute(null);
          removeAttribute.reset();
        }}
        onConfirm={() => deletingAttribute && void removeAttribute.mutate(deletingAttribute)}
        title="Remove this attribute?"
        body={`${deletingAttribute?.key} is removed from ${item.sku}. This can't be undone.`}
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={removeAttribute.pending}
        {...(removeAttribute.error ? { error: problemMessage(removeAttribute.error) } : {})}
      />

      {addingVariant ? (
        <ItemVariantFormDialog
          itemId={id}
          variant={null}
          onClose={() => setAddingVariant(false)}
          onSaved={(saved) => {
            setAddingVariant(false);
            toast.success(`${saved.sku} added.`);
            variantsState.reload();
          }}
        />
      ) : null}

      {editingVariant ? (
        <ItemVariantFormDialog
          itemId={id}
          variant={editingVariant}
          onClose={() => setEditingVariant(null)}
          onSaved={(saved) => {
            setEditingVariant(null);
            toast.success(`${saved.sku} saved.`);
            variantsState.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deletingVariant !== null}
        onClose={() => {
          setDeletingVariant(null);
          removeVariant.reset();
        }}
        onConfirm={() => deletingVariant && void removeVariant.mutate(deletingVariant)}
        title="Remove this variant?"
        body={`${deletingVariant?.sku} is removed from ${item.sku}. This can't be undone.`}
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={removeVariant.pending}
        {...(removeVariant.error ? { error: problemMessage(removeVariant.error) } : {})}
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
