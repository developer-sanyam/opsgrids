/**
 * The invoice form's live tax preview, computed the way finance-service computes the real thing.
 *
 * Integer arithmetic on minor units throughout, with `BigInt` where a product could pass 2^53: a
 * quantity (up to four decimal places) times a unit price, and a taxable value times a rate. Every
 * rounding is half-up away from zero, applied once, exactly as `platform_core.gst` does — so the
 * preview a person reviews is the figure the server will issue, to the paisa, and a credit note
 * rounds as the invoice it reverses.
 *
 * Still a preview: the server recomputes everything and the issued document shows its figures.
 */

import type { Minor } from "@/lib/money";

/** Divide `n` by `d` (both BigInt, `d` > 0), rounding half-up away from zero. */
function divRound(n: bigint, d: bigint): bigint {
  const q = n / d;
  const r = n % d;
  const twice = (r < 0n ? -r : r) * 2n;
  if (twice >= d) return n < 0n ? q - 1n : q + 1n;
  return q;
}

/** `quantity × unitPrice`, rounded once. Quantities carry at most four decimal places. */
export function grossMinor(quantity: number, unitPriceMinor: Minor): Minor {
  const scaledQty = BigInt(Math.round(quantity * 10_000));
  return Number(divRound(scaledQty * BigInt(unitPriceMinor), 10_000n));
}

/** The taxable value after a discount, which always reduces the magnitude (a return line too). */
export function taxableMinor(gross: Minor, discountMinor: Minor): Minor {
  return gross >= 0 ? gross - discountMinor : gross + discountMinor;
}

/** GST on a taxable value at a rate in percent (0.25, 1.5, 18 …), rounded once. */
export function gstAmountMinor(taxable: Minor, ratePct: number): Minor {
  const basisPoints = BigInt(Math.round(ratePct * 100));
  return Number(divRound(BigInt(taxable) * basisPoints, 10_000n));
}

export interface TaxHeads {
  cgst: Minor;
  sgst: Minor;
  igst: Minor;
}

/** Split a line's tax: IGST whole across states; within one, CGST the half toward zero, SGST the rest. */
export function splitTax(tax: Minor, intraState: boolean): TaxHeads {
  if (!intraState) return { cgst: 0, sgst: 0, igst: tax };
  const half = Math.trunc(Math.abs(tax) / 2);
  const cgst = tax < 0 ? -half : half;
  return { cgst, sgst: tax - cgst, igst: 0 };
}

/** Exports and SEZ supplies are inter-state by statute, whatever the two states are. */
export function isIntraState(supplyType: string, sellerState: string | null, placeOfSupply: string | null): boolean {
  if (supplyType === "export" || supplyType === "sez") return false;
  return sellerState !== null && sellerState !== "" && sellerState === placeOfSupply;
}

/** The adjustment that takes a total to whole rupees, half a rupee or more away from zero. */
export function roundOffMinor(total: Minor): Minor {
  return Number(divRound(BigInt(total), 100n)) * 100 - total;
}

export interface PreviewLine {
  quantity: number;
  unitPriceMinor: Minor;
  discountMinor: Minor;
  ratePct: number;
}

export interface RateRow extends TaxHeads {
  ratePct: number;
  taxable: Minor;
}

export interface InvoicePreview extends TaxHeads {
  lines: (TaxHeads & { gross: Minor; taxable: Minor })[];
  byRate: RateRow[];
  taxable: Minor;
  tax: Minor;
  roundOff: Minor;
  total: Minor;
}

/** The whole document's preview: per line, by rate, and the grand total with optional round-off. */
export function previewInvoice(
  lines: readonly PreviewLine[],
  { intraState, reverseCharge, roundOff }: { intraState: boolean; reverseCharge: boolean; roundOff: boolean },
): InvoicePreview {
  const out: InvoicePreview["lines"] = [];
  const rates = new Map<number, RateRow>();
  let taxable = 0;
  let cgst = 0;
  let sgst = 0;
  let igst = 0;
  for (const line of lines) {
    const gross = grossMinor(line.quantity, line.unitPriceMinor);
    const value = taxableMinor(gross, line.discountMinor);
    const heads = reverseCharge ? { cgst: 0, sgst: 0, igst: 0 } : splitTax(gstAmountMinor(value, line.ratePct), intraState);
    out.push({ gross, taxable: value, ...heads });
    taxable += value;
    cgst += heads.cgst;
    sgst += heads.sgst;
    igst += heads.igst;
    const row = rates.get(line.ratePct) ?? { ratePct: line.ratePct, taxable: 0, cgst: 0, sgst: 0, igst: 0 };
    row.taxable += value;
    row.cgst += heads.cgst;
    row.sgst += heads.sgst;
    row.igst += heads.igst;
    rates.set(line.ratePct, row);
  }
  const tax = cgst + sgst + igst;
  const adjustment = roundOff ? roundOffMinor(taxable + tax) : 0;
  return {
    lines: out,
    byRate: [...rates.values()].sort((a, b) => a.ratePct - b.ratePct),
    taxable,
    cgst,
    sgst,
    igst,
    tax,
    roundOff: adjustment,
    total: taxable + tax + adjustment,
  };
}
