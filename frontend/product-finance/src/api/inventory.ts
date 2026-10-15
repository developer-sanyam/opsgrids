/**
 * Inventory items for the invoice line picker — read through the Operations API, never a database
 * join (finance and operations own separate schemas). The caller needs the Operations product and
 * `operations.inventory.item.read`; the invoice form only offers the picker when both are held, and
 * any refusal leaves the free-text line exactly as usable as before.
 *
 * Nothing picked here is trusted server-side: the line copies the item's name, HSN, unit and price
 * as editable starting values, and finance stores only the item id and SKU as a reference.
 */

import { apiRequest, buildQuery, settle } from "@opsgrids/client";

import { USE_FIXTURES } from "./client";

export interface InventoryPick {
  id: string;
  sku: string;
  name: string;
  /** The stock unit code (`kg`, `pcs`, `box`), mapped to a GST unit by the form. */
  uom: string;
  hsn_code: string;
  /** The selling price per stock unit, minor units; null when the item has none set. */
  selling_price_minor: number | null;
  selling_price_currency: string | null;
}

interface InventoryPage {
  items: InventoryPick[];
}

/** The picker's result cap; the combobox says so when exactly this many come back. */
export const ITEM_PICK_CAP = 20;

const FIXTURE_ITEMS: InventoryPick[] = [
  {
    id: "00000000-0000-4000-8000-0000000000a1",
    sku: "ITM-000007",
    name: "Steel bracket, 40 mm",
    uom: "pcs",
    hsn_code: "7326",
    selling_price_minor: 12_345,
    selling_price_currency: "INR",
  },
  {
    id: "00000000-0000-4000-8000-0000000000a2",
    sku: "ITM-000012",
    name: "Installation service",
    uom: "",
    hsn_code: "998719",
    selling_price_minor: null,
    selling_price_currency: null,
  },
];

/** Active items whose SKU or name matches `query`, first page only. */
export async function searchInventoryItems(query: string, signal: AbortSignal): Promise<InventoryPick[]> {
  if (USE_FIXTURES) {
    const q = query.trim().toLowerCase();
    return settle(FIXTURE_ITEMS.filter((i) => `${i.sku} ${i.name}`.toLowerCase().includes(q)));
  }
  const page = await apiRequest<InventoryPage>(
    `/operations/inventory${buildQuery({ q: query.trim(), is_active: "true", size: ITEM_PICK_CAP, page: 1 })}`,
    { signal },
  );
  return page.items;
}
