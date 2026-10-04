import { useRef } from "react";

import { searchInventory } from "@/api/client";
import type { InventoryItemOut } from "@/api/types";
import { Combobox } from "@/ui";
import type { ComboboxOption } from "@/ui";

const COPY = {
  idle: "Start typing a SKU or item name.",
  noMatches: (q: string) => `No inventory item matches "${q}".`,
  noMatchesHint: "Check the spelling, or look the item up on the Inventory page.",
  error: "Couldn't search inventory.",
};

interface MovementItemPickerProps {
  value: ComboboxOption | null;
  onChange: (option: ComboboxOption | null) => void;
  /** The matched item, so the form can read its unit and whether it is lot or roll tracked. */
  onPick: (item: InventoryItemOut | null) => void;
  label?: string;
  error?: string | undefined;
  helper?: string | undefined;
}

/** Looks up a stocked item by SKU or name — `GET /operations/inventory?q=` — for a transfer line,
 *  a reservation or an adjustment. The wire value is the item's id. */
export function MovementItemPicker({ value, onChange, onPick, label = "Item", error, helper }: MovementItemPickerProps) {
  const cache = useRef<Map<string, InventoryItemOut>>(new Map());

  return (
    <Combobox
      label={label}
      value={value}
      onChange={(option) => {
        onChange(option);
        onPick(option ? (cache.current.get(option.id) ?? null) : null);
      }}
      loadOptions={async (q) => {
        const items = await searchInventory(q);
        for (const item of items) cache.current.set(item.id, item);
        return items.map((item) => ({ id: item.id, label: item.sku, sublabel: item.name }));
      }}
      placeholder="Look up a stocked item…"
      copy={COPY}
      {...(error !== undefined ? { error } : {})}
      {...(helper !== undefined ? { helper } : {})}
    />
  );
}
