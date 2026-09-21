import { useRef } from "react";

import { searchInventory } from "@/api/client";
import type { InventoryItemOut } from "@/api/types";
import { Combobox } from "@/ui";
import type { ComboboxOption } from "@/ui";

const SKU_COPY = {
  idle: "Start typing a SKU or item name.",
  noMatches: (q: string) => `No inventory item matches "${q}".`,
  noMatchesHint: "You can still type a free-text SKU below for a service or logistics line.",
  error: "Couldn't search inventory.",
};

function itemOption(item: InventoryItemOut): ComboboxOption {
  return { id: item.sku, label: item.sku, sublabel: item.name };
}

interface SkuPickerProps {
  value: ComboboxOption | null;
  onChange: (option: ComboboxOption | null) => void;
  /** The matched inventory item, so the line editor can prefill description/uom/price. */
  onPick: (item: InventoryItemOut | null) => void;
  error?: string;
}

/**
 * Looks up an existing inventory item by SKU to prefill a PO line's description, unit and price
 * — `GET /operations/inventory?q=`. A line's `sku` stays free text on the wire either way: a SKU
 * with no match is a service/logistics line, not an error.
 */
export function SkuPicker({ value, onChange, onPick, error }: SkuPickerProps) {
  const cacheRef = useRef<Map<string, InventoryItemOut>>(new Map());

  return (
    <Combobox
      label="SKU"
      value={value}
      onChange={(option) => {
        onChange(option);
        onPick(option ? (cacheRef.current.get(option.id) ?? null) : null);
      }}
      loadOptions={async (q) => {
        const items = await searchInventory(q);
        for (const item of items) cacheRef.current.set(item.sku, item);
        return items.map(itemOption);
      }}
      placeholder="Look up a stocked item…"
      copy={SKU_COPY}
      {...(error !== undefined ? { error } : {})}
    />
  );
}
