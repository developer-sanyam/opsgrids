import type { StockRow } from "@/api/scan";
import { formatQty, qtyToMilli } from "@/lib/quantity";

const QTY = /^\d+(\.\d{1,3})?$/;

/** `""` when `value` is a quantity this bucket can give, else the sentence saying why not. */
export function checkQuantity(value: string, bucket: StockRow): string {
  const text = value.trim();
  if (!QTY.test(text)) return "Enter a quantity such as 12 or 82.5 (up to 3 decimals)";
  const milli = qtyToMilli(text);
  if (milli <= 0) return "The quantity must be more than zero";
  if (milli > qtyToMilli(bucket.available_qty)) {
    return `Only ${formatQty(bucket.available_qty)} ${bucket.uom} is free to move here`;
  }
  return "";
}
