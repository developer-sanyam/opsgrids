import { useRef } from "react";

import { searchOrderableItems } from "@/api/client";
import type { InventoryItemOut } from "@/api/types";
import { Combobox } from "@/ui";
import type { ComboboxOption } from "@/ui";

const ITEM_COPY = {
  idle: "Start typing a SKU or item name.",
  noMatches: (q: string) => `No inventory item matches "${q}".`,
  noMatchesHint: "An order line can only name a real stocked item — check the spelling.",
  error: "Couldn't search inventory.",
};

function itemOption(item: InventoryItemOut): ComboboxOption {
  return { id: item.id, label: item.sku, sublabel: item.name };
}

interface OrderItemPickerProps {
  value: ComboboxOption | null;
  onChange: (option: ComboboxOption | null) => void;
  /** The matched inventory item, so the line editor can prefill its selling price and show its
   *  available stock. `null` when the selection was cleared. */
  onPick: (item: InventoryItemOut | null) => void;
  error?: string;
}

/**
 * Looks up a real inventory item for an order line — `GET /operations/inventory?q=`. Unlike the
 * purchase-order `SkuPicker`, the wire value here is `inventory_item_id` (a UUID), never a
 * free-text SKU: `OrderLineIn.inventory_item_id` must name a real item in the organization, so an
 * order line has no "unmatched SKU" escape hatch a PO line does.
 */
export function OrderItemPicker({ value, onChange, onPick, error }: OrderItemPickerProps) {
  const cacheRef = useRef<Map<string, InventoryItemOut>>(new Map());

  return (
    <Combobox
      label="Item"
      value={value}
      onChange={(option) => {
        onChange(option);
        onPick(option ? (cacheRef.current.get(option.id) ?? null) : null);
      }}
      loadOptions={async (q) => {
        const items = await searchOrderableItems(q);
        for (const item of items) cacheRef.current.set(item.id, item);
        return items.map(itemOption);
      }}
      placeholder="Look up a stocked item…"
      copy={ITEM_COPY}
      {...(error !== undefined ? { error } : {})}
    />
  );
}
