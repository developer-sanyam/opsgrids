import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { deleteInventoryItem, listInventory } from "@/api/client";
import type { InventoryItemOut } from "@/api/types";
import { CATEGORY_LABEL } from "@/data/derive";
import { inventoryImport } from "@/data/importExport/inventory";
import type { InventoryFilters } from "@/data/importExport/inventory";
import { useItemCategoryOptions } from "@/data/useItemCategoryOptions";
import { useWarehouseOptions } from "@/data/useWarehouseOptions";
import {
  Can,
  problemMessage,
  useAsync,
  useCan,
  useImportExport,
  useMutation,
} from "@opsgrids/client";
import { formatMoney, formatMoneyCompact, formatNumber } from "@/lib/format";
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
  SearchInput,
  Select,
  Skeleton,
  StockPill,
  Tag,
} from "@/ui";
import type { Column, StockLevel } from "@/ui";
import { ImportExportButton, LayersIcon, PlusIcon, useDebouncedValue, useToast } from "@opsgrids/ui";
import { InventoryItemFormDialog } from "@/forms/InventoryItemFormDialog";
import { StockAdjustmentDialog } from "@/forms/StockAdjustmentDialog";

type Filter = "all" | "low";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All items" },
  { value: "low", label: "Low stock" },
];

type SortKey = "sku" | "on_hand" | "value";

function levelOf(item: InventoryItemOut): StockLevel {
  const onHand = qtyToMilli(item.on_hand_qty);
  if (onHand === 0) return "out";
  if (onHand <= item.reorder_point * 1000) return "low";
  return "ok";
}

export function InventoryPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [filter, setFilter] = useState<Filter>("all");
  const [warehouse, setWarehouse] = useState<string | "all">("all");
  const [itemCategoryId, setItemCategoryId] = useState<string | "all">("all");
  const [sort, setSort] = useState<SortKey>("value");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<InventoryItemOut | null>(null);
  const [adjusting, setAdjusting] = useState<InventoryItemOut | null>(null);
  const [deleting, setDeleting] = useState<InventoryItemOut | null>(null);
  const canWrite = useCan("operations.inventory.item.write", "ORG");
  const canAdjust = useCan("operations.inventory.stock.adjust", "ORG");
  const warehouses = useWarehouseOptions();
  const itemCategories = useItemCategoryOptions();

  const loader = () =>
    listInventory({
      q: debouncedSearch,
      warehouse,
      item_category_id: itemCategoryId,
      sort,
      page,
      size,
      ...(filter === "low" ? { low_stock: true } : {}),
    });
  const state = useAsync(loader, [
    debouncedSearch,
    filter,
    warehouse,
    itemCategoryId,
    sort,
    page,
    size,
  ]);

  // The export is the whole list under these filters, not the page on screen.
  const filters = useMemo<InventoryFilters>(
    () => ({
      q: debouncedSearch,
      warehouse,
      item_category_id: itemCategoryId,
      sort,
      ...(filter === "low" ? { low_stock: true } : {}),
    }),
    [debouncedSearch, warehouse, itemCategoryId, sort, filter],
  );
  // `state.reload` keeps one identity for the life of the screen. The dialog stays open on its
  // result, so the rows it just created appear behind it as soon as the run ends.
  const dx = useImportExport(inventoryImport, { filters, onImported: state.reload });

  const remove = useMutation((item: InventoryItemOut) => deleteInventoryItem(item.id), {
    onSuccess: (_result, item) => {
      setDeleting(null);
      toast.success(`${item.name} deleted.`);
      state.reload();
    },
  });

  const filtersActive =
    debouncedSearch !== "" || filter !== "all" || warehouse !== "all" || itemCategoryId !== "all";

  const columns: Column<InventoryItemOut>[] = [
    {
      key: "sku",
      header: "SKU",
      sortKey: "sku",
      render: (it) => <span className="tnum text-xs font-semibold text-muted">{it.sku}</span>,
    },
    {
      key: "item",
      header: "Item",
      render: (it) => (
        <button
          type="button"
          onClick={() => navigate(`/inventory/${it.id}`)}
          className="flex flex-col text-left leading-tight"
        >
          <span className="font-semibold text-ink hover:text-accent transition-colors">
            {it.name}
          </span>
          <span className="text-xs text-faint">{CATEGORY_LABEL[it.category]}</span>
        </button>
      ),
    },
    {
      key: "warehouse",
      header: "Warehouse",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (it) => <Tag>{it.warehouse}</Tag>,
    },
    {
      key: "on_hand",
      header: "On hand",
      align: "right",
      sortKey: "on_hand",
      render: (it) => (
        <span className="tnum font-semibold text-ink whitespace-nowrap">
          {formatQty(it.on_hand_qty)} <span className="text-xs font-normal text-muted">{it.uom}</span>
        </span>
      ),
    },
    {
      key: "reorder",
      header: "Reorder",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (it) => <span className="tnum text-muted">{formatNumber(it.reorder_point)}</span>,
    },
    {
      key: "value",
      header: "Value",
      align: "right",
      sortKey: "value",
      render: (it) => (
        <span className="tnum font-semibold text-ink">
          {formatMoney(stockValueMinor(it.on_hand_qty, it.unit_value_minor), it.currency)}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      align: "right",
      render: (it) => (
        <div className="flex justify-end">
          <StockPill level={levelOf(it)} />
        </div>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (it) => (
        <RowActions
          label={it.name}
          actions={[
            { label: "View", onSelect: () => navigate(`/inventory/${it.id}`) },
            { label: "Adjust stock", onSelect: () => setAdjusting(it), hidden: !canAdjust },
            { label: "Edit", onSelect: () => setEditing(it), hidden: !canWrite },
            {
              label: "Delete",
              tone: "danger",
              onSelect: () => setDeleting(it),
              hidden: !canWrite,
              disabled: qtyToMilli(it.on_hand_qty) !== 0,
              disabledReason: "Adjust stock to zero before deleting this item.",
            },
          ]}
        />
      ),
    },
  ];

  const totalValue =
    state.status === "success"
      ? state.data.items.reduce((s, it) => s + stockValueMinor(it.on_hand_qty, it.unit_value_minor), 0)
      : 0;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Inventory"
        title="Stock on Hand"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-surface shadow-card px-4 h-11 hidden sm:inline-flex items-center gap-2 text-sm font-semibold">
              <span className="text-muted font-medium">Page value</span>
              <span className="tnum">{formatMoneyCompact(totalValue)}</span>
            </span>
            <Button variant="secondary" onClick={() => navigate("/inventory/labels/item")}>
              Labels
            </Button>
            <Can permission="operations.inventory.stock.read" scope="ORG">
              <Button variant="secondary" onClick={() => navigate("/inventory/scan")}>
                Scan
              </Button>
            </Can>
            <ImportExportButton
              meta={dx.meta}
              state={dx.state}
              actions={dx.actions}
              canImport={canWrite}
              canExport
            />
            <Can permission="operations.inventory.item.write" scope="ORG">
              <Button onClick={() => setCreating(true)} leadingIcon={<PlusIcon className="size-4" />}>
                Add item
              </Button>
            </Can>
          </div>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3">
          <SearchInput
            label="Search inventory"
            placeholder="Search by SKU, item or warehouse…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            containerClassName="lg:max-w-sm"
          />
          <Select
            label="Warehouse"
            containerClassName="lg:w-48"
            value={warehouse}
            onChange={(e) => {
              setWarehouse(e.target.value);
              setPage(1);
            }}
            options={[{ value: "all", label: "All warehouses" }, ...warehouses.options]}
          />
          <Select
            label="Item category"
            containerClassName="lg:w-48"
            value={itemCategoryId}
            onChange={(e) => {
              setItemCategoryId(e.target.value);
              setPage(1);
            }}
            options={[{ value: "all", label: "All categories" }, ...itemCategories.options]}
          />
          <Select
            label="Sort inventory"
            containerClassName="lg:ml-auto lg:w-52"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            options={[
              { value: "value", label: "Value high–low" },
              { value: "on_hand", label: "On hand high–low" },
              { value: "sku", label: "SKU A–Z" },
            ]}
          />
        </div>
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
          {FILTERS.map((f) => (
            <Pill
              key={f.value}
              active={filter === f.value}
              onClick={() => {
                setFilter(f.value);
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
          <ErrorState
            title="Couldn't load inventory"
            message={problemMessage(state.error)}
            onRetry={state.reload}
          />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(it) => it.id}
              caption="Inventory stock levels"
              onRowClick={(it) => navigate(`/inventory/${it.id}`)}
              emptyState={
                <EmptyState
                  icon={<LayersIcon className="size-6" />}
                  title={filtersActive ? "No items match these filters" : "No inventory yet"}
                  description={
                    filtersActive
                      ? "Try a different warehouse or view, or clear the search."
                      : "Add the first item to start tracking stock."
                  }
                  action={
                    filtersActive ? null : (
                      <Can permission="operations.inventory.item.write" scope="ORG">
                        <Button size="sm" onClick={() => setCreating(true)}>
                          Add item
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
              noun="items"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {creating ? (
        <InventoryItemFormDialog
          item={null}
          onClose={() => setCreating(false)}
          onSaved={(item) => {
            setCreating(false);
            toast.success(`${item.name} created.`);
            state.reload();
          }}
        />
      ) : null}

      {editing ? (
        <InventoryItemFormDialog
          item={editing}
          onClose={() => setEditing(null)}
          onSaved={(item) => {
            setEditing(null);
            toast.success(`${item.name} saved.`);
            state.reload();
          }}
        />
      ) : null}

      {adjusting ? (
        <StockAdjustmentDialog
          item={adjusting}
          onClose={() => setAdjusting(null)}
          onSaved={(item) => {
            setAdjusting(null);
            toast.success(`Stock adjusted — ${formatQty(item.on_hand_qty)} ${item.uom} on hand.`);
            state.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          remove.reset();
        }}
        onConfirm={() => deleting && void remove.mutate(deleting)}
        title="Delete this item?"
        body={`${deleting?.sku} — ${deleting?.name} is removed from inventory. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}

