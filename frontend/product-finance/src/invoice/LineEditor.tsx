import { useState } from "react";

import { ITEM_PICK_CAP, searchInventoryItems } from "@/api/inventory";
import type { InventoryPick } from "@/api/inventory";
import { formatMoney, minorToInput } from "@/lib/money";
import type { Minor } from "@/lib/money";
import { Button, Select, TextField } from "@/ui";
import { Combobox } from "@opsgrids/ui";
import type { ComboboxOption } from "@opsgrids/ui";

import type { LineDraft } from "./lineDraft";
import { GST_RATE_OPTIONS, UQC_OPTIONS, uqcForInventoryUnit } from "./vocabulary";

/** What the picker hands the line: editable starting values, never locked fields. */
function fromItem(line: LineDraft, item: InventoryPick): LineDraft {
  const price =
    item.selling_price_minor !== null && (item.selling_price_currency ?? "INR") === "INR"
      ? minorToInput(item.selling_price_minor)
      : line.unitPrice;
  return {
    ...line,
    description: item.name,
    hsnSac: item.hsn_code || line.hsnSac,
    unit: uqcForInventoryUnit(item.uom) ?? line.unit,
    unitPrice: price,
    itemId: item.id,
    itemSku: item.sku,
  };
}

interface LineEditorProps {
  index: number;
  line: LineDraft;
  /** The line's computed taxable value and tax, for the figure under it; null while unparsable. */
  figures: { taxable: Minor; tax: Minor } | null;
  /** Offer the inventory picker: the caller holds the Operations product and item read. */
  canPickItems: boolean;
  canRemove: boolean;
  onChange: (patch: Partial<LineDraft>) => void;
  onRemove: () => void;
  errorFor: (field: string) => string | undefined;
}

/**
 * One invoice line: an optional pick from inventory, then the facts Rule 46 prints for it —
 * description, HSN/SAC, quantity and unit, rate, discount, and the GST rate. Typing a description
 * without picking is the free-text path and is always available.
 */
export function LineEditor({ index, line, figures, canPickItems, canRemove, onChange, onRemove, errorFor }: LineEditorProps) {
  const [picked, setPicked] = useState<ComboboxOption | null>(
    line.itemId ? { id: line.itemId, label: line.description, sublabel: line.itemSku ?? undefined } : null,
  );
  const [items, setItems] = useState<Map<string, InventoryPick>>(new Map());
  const n = index + 1;

  async function load(query: string, signal: AbortSignal): Promise<ComboboxOption[]> {
    const found = await searchInventoryItems(query, signal);
    setItems(new Map(found.map((i) => [i.id, i])));
    return found.map((i) => ({ id: i.id, label: i.name, sublabel: i.sku }));
  }

  return (
    <div className="flex flex-col gap-3 rounded-tile border border-line p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-faint">Line {n}</span>
        <Button type="button" variant="ghost" size="sm" onClick={onRemove} disabled={!canRemove} aria-label={`Remove line ${n}`}>
          Remove
        </Button>
      </div>
      {canPickItems ? (
        <Combobox
          label="Item from inventory"
          value={picked}
          onChange={(option) => {
            setPicked(option);
            if (option === null) {
              onChange({ itemId: null, itemSku: null });
              return;
            }
            const item = items.get(option.id);
            if (item) onChange(fromItem(line, item));
          }}
          loadOptions={load}
          cap={ITEM_PICK_CAP}
          helper="Optional. Fills the description, HSN, unit and price, which you can still edit."
          placeholder="Search by name or SKU"
          copy={{
            idle: "Type an item name or SKU.",
            noMatches: (q) => `No active item matches “${q}”.`,
            noMatchesHint: "Type the description yourself below instead.",
            error: "Couldn't search inventory. Type the line yourself below.",
          }}
        />
      ) : null}
      <div className="grid grid-cols-1 items-start gap-3 sm:grid-cols-6">
        <div className="sm:col-span-4">
          <TextField
            label="Description"
            value={line.description}
            onChange={(e) => onChange({ description: e.target.value })}
            required
            error={errorFor(`lines.${index}.description`)}
          />
        </div>
        <div className="sm:col-span-2">
          <TextField
            label="HSN/SAC"
            value={line.hsnSac}
            inputMode="numeric"
            maxLength={8}
            onChange={(e) => onChange({ hsnSac: e.target.value.replace(/\D/g, "") })}
            optional
            helper="4, 6 or 8 numerals"
            error={errorFor(`lines.${index}.hsn_sac`)}
          />
        </div>
        <TextField
          label="Quantity"
          inputMode="decimal"
          value={line.quantity}
          onChange={(e) => onChange({ quantity: e.target.value })}
          required
        />
        <Select
          label="Unit"
          value={line.unit}
          onChange={(e) => onChange({ unit: e.target.value })}
          placeholder="No unit (service)"
          options={UQC_OPTIONS}
        />
        <TextField
          label="Rate (₹)"
          inputMode="decimal"
          value={line.unitPrice}
          onChange={(e) => onChange({ unitPrice: e.target.value })}
          required
        />
        <TextField
          label="Discount (₹)"
          inputMode="decimal"
          value={line.discount}
          onChange={(e) => onChange({ discount: e.target.value })}
          optional
        />
        <div className="sm:col-span-2">
          <Select
            label="GST rate"
            value={line.ratePct}
            onChange={(e) => onChange({ ratePct: e.target.value })}
            options={GST_RATE_OPTIONS}
            error={errorFor(`lines.${index}.tax_rate_pct`)}
          />
        </div>
      </div>
      {figures ? (
        <p className="text-xs text-muted tnum" aria-live="polite">
          Taxable value {formatMoney(figures.taxable)} · GST {formatMoney(figures.tax)}
          {line.itemSku ? ` · ${line.itemSku}` : ""}
        </p>
      ) : null}
    </div>
  );
}
