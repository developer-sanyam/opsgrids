import { useMemo, useState } from "react";

import { listInventory } from "@/api/client";
import type { InventoryItemOut } from "@/api/types";
import { CATEGORY_LABEL, inventoryValueMinor, isLowStock, isOutOfStock } from "@/data/derive";
import { useAsync } from "@opsgrids/client";
import { formatMoney, formatMoneyCompact, formatNumber } from "@/lib/format";
import {
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
  StockPill,
  Tag,
} from "@/ui";
import type { Column, StockLevel } from "@/ui";
import { LayersIcon } from "@opsgrids/ui";

type Filter = "all" | "low" | "out";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All items" },
  { value: "low", label: "Low stock" },
  { value: "out", label: "Out of stock" },
];

type SortKey = "name" | "warehouse" | "on_hand" | "value";
interface Sort {
  key: SortKey;
  dir: "asc" | "desc";
}

const NO_ROWS: InventoryItemOut[] = [];

interface Row extends InventoryItemOut {
  _value: number;
  _level: StockLevel;
}

function levelOf(item: InventoryItemOut): StockLevel {
  if (isOutOfStock(item)) return "out";
  if (isLowStock(item)) return "low";
  return "ok";
}

export function InventoryPage() {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>({ key: "value", dir: "desc" });

  const state = useAsync(listInventory, []);
  const items = state.status === "success" ? state.data.items : NO_ROWS;

  const totalValue = useMemo(() => inventoryValueMinor(items), [items]);
  const lowCount = useMemo(() => items.filter(isLowStock).length, [items]);

  const rows: Row[] = useMemo(() => {
    const q = search.trim().toLowerCase();
    const enriched = items
      .filter((it) => {
        const matchesQuery =
          q === "" ||
          it.sku.toLowerCase().includes(q) ||
          it.name.toLowerCase().includes(q) ||
          it.warehouse.toLowerCase().includes(q);
        const level = levelOf(it);
        const matchesFilter =
          filter === "all" ||
          (filter === "low" && level !== "ok") ||
          (filter === "out" && level === "out");
        return matchesQuery && matchesFilter;
      })
      .map((it) => ({ ...it, _value: it.on_hand * it.unit_value_minor, _level: levelOf(it) }));

    const dir = sort.dir === "asc" ? 1 : -1;
    enriched.sort((a, b) => {
      switch (sort.key) {
        case "on_hand":
          return (a.on_hand - b.on_hand) * dir;
        case "value":
          return (a._value - b._value) * dir;
        case "warehouse":
          return a.warehouse.localeCompare(b.warehouse) * dir;
        case "name":
        default:
          return a.name.localeCompare(b.name) * dir;
      }
    });
    return enriched;
  }, [items, search, filter, sort]);

  function onSort(key: string) {
    setSort((prev) =>
      prev.key === key
        ? { key: prev.key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key: key as SortKey, dir: "asc" },
    );
  }

  const columns: Column<Row>[] = [
    {
      key: "sku",
      header: "SKU",
      render: (it) => <span className="tnum text-xs font-semibold text-muted">{it.sku}</span>,
    },
    {
      key: "item",
      header: "Item",
      sortKey: "name",
      render: (it) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{it.name}</span>
          <span className="text-xs text-faint">{CATEGORY_LABEL[it.category]}</span>
        </div>
      ),
    },
    {
      key: "warehouse",
      header: "Warehouse",
      sortKey: "warehouse",
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
          {formatNumber(it.on_hand)} <span className="text-xs font-normal text-muted">{it.uom}</span>
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
      render: (it) => <span className="tnum font-semibold text-ink">{formatMoney(it._value)}</span>,
    },
    {
      key: "status",
      header: "Status",
      align: "right",
      render: (it) => (
        <div className="flex justify-end">
          <StockPill level={it._level} />
        </div>
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Inventory"
        title="Stock on Hand"
        actions={
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-surface shadow-card px-4 h-11 hidden sm:inline-flex items-center gap-2 text-sm font-semibold">
              <span className="text-muted font-medium">Value</span>
              <span className="tnum">{formatMoneyCompact(totalValue)}</span>
            </span>
            <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
              <LayersIcon className="size-4 text-muted" />
              <span className="tnum">{items.length}</span>
              <span className="text-muted font-medium">SKUs</span>
            </span>
          </div>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3">
          <SearchInput
            label="Search inventory"
            placeholder="Search by SKU, item or warehouse…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            {FILTERS.map((f) => (
              <Pill key={f.value} active={filter === f.value} onClick={() => setFilter(f.value)}>
                {f.label}
                {f.value === "low" && lowCount > 0 ? (
                  <span className="ml-1 tnum text-xs opacity-80">{lowCount}</span>
                ) : null}
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
        ) : state.status === "error" ? (
          <ErrorState message={state.error.message} />
        ) : (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(it) => it.id}
            caption="Inventory stock levels"
            sort={sort}
            onSort={onSort}
            emptyState={
              <EmptyState
                icon={<LayersIcon className="size-6" />}
                title="No matching items"
                description="No stock matches these filters. Try a different view or clear the search."
              />
            }
          />
        )}
      </Card>
    </div>
  );
}
