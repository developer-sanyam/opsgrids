import { useRef } from "react";

import { searchInventory } from "@/api/client";
import type { InventoryItemOut } from "@/api/types";
import { Combobox } from "@/ui";
import type { ComboboxOption } from "@/ui";

const COPY = {
  idle: "Start typing a SKU or item name.",
  noMatches: (q: string) => `No inventory item matches "${q}".`,
  noMatchesHint: "Check the SKU, or add the item on the Inventory screen first.",
  error: "Couldn't search inventory.",
};

/** Which tracking an item must have for the thing being made: a lot only exists for a lot-tracked
 *  item, a roll for a roll-tracked one. */
export type RequiredTracking = "track_lot" | "track_roll";

interface ItemPickerProps {
  value: InventoryItemOut | null;
  onChange: (item: InventoryItemOut | null) => void;
  /** Only items with this tracking flag are offered. */
  requires?: RequiredTracking | undefined;
  label?: string | undefined;
  error?: string | undefined;
  helper?: string | undefined;
}

/**
 * Looks up an inventory item — `GET /operations/inventory?q=`. The picked row is kept in a cache
 * keyed by id, so the caller gets the whole item (its UOM and tracking flags drive the rest of the
 * form), not just an id.
 */
export function ItemPicker({ value, onChange, requires, label = "Item", error, helper }: ItemPickerProps) {
  const cache = useRef(new Map<string, InventoryItemOut>());
  const selected: ComboboxOption | null = value
    ? { id: value.id, label: value.sku, sublabel: value.name }
    : null;
  return (
    <Combobox
      label={label}
      value={selected}
      onChange={(option) => onChange(option ? (cache.current.get(option.id) ?? null) : null)}
      loadOptions={async (q) => {
        const items = (await searchInventory(q)).filter((item) => requires === undefined || item[requires]);
        for (const item of items) cache.current.set(item.id, item);
        return items.map((item) => ({ id: item.id, label: item.sku, sublabel: item.name }));
      }}
      placeholder="Look up an item…"
      copy={COPY}
      {...(error !== undefined ? { error } : {})}
      {...(helper !== undefined ? { helper } : {})}
    />
  );
}
