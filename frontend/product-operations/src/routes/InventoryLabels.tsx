import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { listLabelItems } from "@/api/scan";
import type { LabelItem } from "@/api/scan";
import { formatPayload, SCAN_TYPES } from "@/lib/scanPayload";
import { unprintableReason } from "@/lib/labelItems";
import type { ScanType } from "@/lib/scanPayload";
import { useWarehouseIdOptions } from "@/data/useWarehouseOptions";
import { problemMessage, useAsync } from "@opsgrids/client";
import { Barcode128, Button, Card, EmptyState, ErrorState, QrCode, SearchInput, Select, Skeleton } from "@/ui";
import { ArrowRightIcon, useDebouncedValue } from "@opsgrids/ui";

const TYPE_PLURAL: Record<ScanType, string> = {
  item: "Items",
  lot: "Lots",
  roll: "Rolls",
  location: "Locations",
};

type Size = "small" | "large";

const GRID: Record<Size, string> = {
  small: "grid-cols-2 sm:grid-cols-3 print:grid-cols-3",
  large: "grid-cols-1 sm:grid-cols-2 print:grid-cols-2",
};

function isScanType(value: string): value is ScanType {
  return (SCAN_TYPES as readonly string[]).includes(value);
}

/**
 * `/inventory/labels/:type` — print labels for items, lots, rolls or locations (inventory
 * Phase 9, ADR-0027), the same pattern as a zone's location labels: client-rendered, its own
 * route with no shell around it, printed with `window.print()`.
 *
 * Every label carries the QR (`og:<type>:<code>`, what the scan screen resolves) **and** a Code
 * 128 of the plain code, because a handheld laser reads the bar code and a phone reads the QR.
 */
export function InventoryLabelsPage() {
  const { type: typeParam = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const type = isScanType(typeParam) ? typeParam : null;
  const [search, setSearch] = useState("");
  const q = useDebouncedValue(search);
  const [size, setSize] = useState<Size>("small");
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const warehouses = useWarehouseIdOptions();
  const warehouseId = searchParams.get("warehouse") ?? warehouses.options[0]?.value ?? "";

  const loader = () =>
    type
      ? listLabelItems(type, { q, warehouseId, limit: 100 })
      : Promise.resolve([] as LabelItem[]);
  const state = useAsync(loader, [type, q, warehouseId]);

  const items = useMemo(() => (state.status === "success" ? state.data : []), [state]);
  const selected = useMemo(
    () => items.filter((i) => !excluded.has(i.id) && unprintableReason(i) === ""),
    [items, excluded],
  );

  const toggle = (id: string) =>
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="min-h-dvh bg-ground">
      <div className="print:hidden flex flex-col gap-4 border-b border-line bg-surface px-6 py-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <Link
            to="/inventory"
            className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted transition-colors hover:text-ink"
          >
            <ArrowRightIcon className="size-4 rotate-180" />
            Back to inventory
          </Link>
          <Button onClick={() => window.print()} disabled={selected.length === 0}>
            Print {selected.length} label{selected.length === 1 ? "" : "s"}
          </Button>
        </div>
        <nav aria-label="Label type" className="flex flex-wrap gap-2">
          {SCAN_TYPES.map((t) => (
            <Link
              key={t}
              to={`/inventory/labels/${t}`}
              aria-current={t === type ? "page" : undefined}
              className={
                "rounded-full border px-4 py-2 text-sm font-semibold " +
                (t === type
                  ? "border-ink bg-ink text-surface"
                  : "border-field-edge bg-surface text-ink hover:bg-sunken")
              }
            >
              {TYPE_PLURAL[t]}
            </Link>
          ))}
        </nav>
        {type ? (
          <div className="flex flex-wrap items-end gap-3">
            {type === "location" ? (
              <Select
                label="Warehouse"
                value={warehouseId}
                onChange={(e) => setSearchParams({ warehouse: e.target.value })}
                options={warehouses.options}
                containerClassName="min-w-56"
              />
            ) : (
              <SearchInput
                label={`Search ${TYPE_PLURAL[type].toLowerCase()}`}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                containerClassName="min-w-64"
              />
            )}
            <Select
              label="Label size"
              value={size}
              onChange={(e) => setSize(e.target.value as Size)}
              options={[
                { value: "small", label: "Small (3 across)" },
                { value: "large", label: "Large (2 across)" },
              ]}
            />
            <Button variant="secondary" onClick={() => setExcluded(new Set())}>
              Select all
            </Button>
            <Button variant="secondary" onClick={() => setExcluded(new Set(items.map((i) => i.id)))}>
              Select none
            </Button>
          </div>
        ) : null}
      </div>

      <div className="mx-auto flex max-w-5xl flex-col gap-6 p-6 print:max-w-none print:p-0">
        {!type ? (
          <ErrorState title="Unknown label type" message="Pick items, lots, rolls or locations above." />
        ) : state.status === "loading" ? (
          <div className="grid grid-cols-2 gap-4 print:hidden">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-48 w-full rounded-tile" />
            ))}
          </div>
        ) : state.status === "error" ? (
          <ErrorState
            title="Couldn't load the list"
            message={problemMessage(state.error)}
            onRetry={state.reload}
          />
        ) : items.length === 0 ? (
          <EmptyState
            title={type === "location" ? "No locations with a barcode" : "Nothing to print"}
            description={
              type === "location"
                ? "Give the locations a barcode first; a label is the barcode, printed."
                : "Nothing matches that search."
            }
          />
        ) : (
          <>
            <p className="print:hidden text-sm text-muted">
              {items.length} shown{items.length >= 100 ? " (the first 100 — search to narrow)" : ""}.
              Untick a record to leave its label out.
            </p>
            <ul className={`grid gap-4 ${GRID[size]}`}>
              {items.map((item) => {
                const reason = unprintableReason(item);
                const on = !excluded.has(item.id) && reason === "";
                return (
                  <li key={item.id} className={on ? "" : "print:hidden"}>
                    <LabelCard item={item} on={on} reason={reason} onToggle={() => toggle(item.id)} />
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

function LabelCard({
  item,
  on,
  reason,
  onToggle,
}: {
  item: LabelItem;
  on: boolean;
  reason: string;
  onToggle: () => void;
}) {
  return (
    <Card className={`flex break-inside-avoid flex-col items-center gap-2 p-4 text-center ${on ? "" : "opacity-50"}`}>
      <label className="print:hidden flex w-full items-center gap-2 text-left text-xs text-muted">
        <input
          type="checkbox"
          checked={on}
          disabled={reason !== ""}
          onChange={onToggle}
          aria-label={`Print the label for ${item.title}`}
        />
        {reason || "Print this label"}
      </label>
      <span className="text-lg font-extrabold text-ink">{item.title}</span>
      <span className="text-xs text-ink-soft">{item.subtitle}</span>
      {reason === "" ? (
        <>
          <QrCode
            value={formatPayload(item.type, item.code)}
            label={`QR code for ${item.title}`}
            size={120}
          />
          <Barcode128 value={item.code} label={`Bar code for ${item.title}`} className="w-full" />
          <span className="tnum text-xs font-semibold text-ink">{item.code}</span>
        </>
      ) : null}
    </Card>
  );
}
