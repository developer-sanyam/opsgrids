import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { getInventoryItem } from "@/api/client";
import type { LedgerKind } from "@/api/trackingTypes";
import type { InventoryItemOut, StockStatus } from "@/api/types";

/** What the stock and ledger screens can be narrowed by. `""` is "not filtered". */
export interface TrackingFilterState {
  item: InventoryItemOut | null;
  warehouseId: string;
  locationId: string;
  lotId: string;
  rollId: string;
  kind: LedgerKind | "";
  status: StockStatus | "";
  from: string;
  to: string;
}

const EMPTY: TrackingFilterState = {
  item: null,
  warehouseId: "",
  locationId: "",
  lotId: "",
  rollId: "",
  kind: "",
  status: "",
  from: "",
  to: "",
};

/**
 * Filter state for a stock or ledger screen, seeded from the address (`?item=&lot=&roll=&location=`)
 * so a link from a lot or a roll lands already narrowed. An item named in the address is fetched so
 * the picker can show it; the filters apply to the lot and roll straight away.
 */
export function useTrackingFilters(): {
  filters: TrackingFilterState;
  setFilter: <K extends keyof TrackingFilterState>(key: K, value: TrackingFilterState[K]) => void;
  clear: () => void;
  active: boolean;
  itemId: string;
} {
  const [params] = useSearchParams();
  const [filters, setFilters] = useState<TrackingFilterState>(() => ({
    ...EMPTY,
    lotId: params.get("lot") ?? "",
    rollId: params.get("roll") ?? "",
    locationId: params.get("location") ?? "",
  }));
  const itemParam = params.get("item") ?? "";
  const [itemId, setItemId] = useState(itemParam);

  useEffect(() => {
    if (itemParam === "") return;
    let live = true;
    getInventoryItem(itemParam)
      .then((item) => {
        if (live) setFilters((current) => ({ ...current, item }));
      })
      .catch(() => {
        // The item filter is a convenience from a link; if it can't load, the list is simply wider.
        if (live) setItemId("");
      });
    return () => {
      live = false;
    };
  }, [itemParam]);

  const selectedItemId = filters.item?.id ?? itemId;
  return {
    filters,
    itemId: selectedItemId,
    setFilter: (key, value) => {
      setFilters((current) => {
        const next = { ...current, [key]: value };
        // Narrowing is hierarchical: a new item invalidates its lot and roll; a new warehouse its location.
        if (key === "item") {
          next.lotId = "";
          next.rollId = "";
        }
        if (key === "warehouseId") next.locationId = "";
        if (key === "lotId") next.rollId = "";
        return next;
      });
      if (key === "item") setItemId((value as InventoryItemOut | null)?.id ?? "");
    },
    clear: () => {
      setFilters(EMPTY);
      setItemId("");
    },
    active:
      selectedItemId !== "" ||
      filters.warehouseId !== "" ||
      filters.locationId !== "" ||
      filters.lotId !== "" ||
      filters.rollId !== "" ||
      filters.kind !== "" ||
      filters.status !== "" ||
      filters.from !== "" ||
      filters.to !== "",
  };
}

