import type { SupplierField } from "@/api/types";

/**
 * The statutory vocabularies the invoice form offers: GST rate slabs and unit quantity codes.
 * Mirrors `services/finance/app/domain.py` (`GST_RATE_BANDS`, `UQC_CODES`); the server refuses
 * anything outside them, so offering only these keeps a person from typing a value that fails.
 */

/**
 * The slabs offered first: the two standard rates and the demerit rate of the September 2025
 * rationalisation, plus nil. 12 % and 28 % remain lawful (a credit note against an older invoice,
 * tobacco goods), and 0.25 / 1.5 / 3 % are the stones and metals bands, so they follow in a group
 * of their own rather than being hidden.
 */
export const CURRENT_GST_RATES = ["0", "5", "18", "40"] as const;
export const OTHER_GST_RATES = ["0.25", "1.5", "3", "12", "28"] as const;

export const GST_RATE_OPTIONS: { value: string; label: string }[] = [
  ...CURRENT_GST_RATES.map((r) => ({ value: r, label: `${r}%` })),
  ...OTHER_GST_RATES.map((r) => ({ value: r, label: `${r}% (special or pre-Sept 2025)` })),
];

/** The first day the 40 % slab exists; the server refuses it on an earlier document. */
export const FORTY_PERCENT_FROM = "2025-09-22";

/** GST Unit Quantity Codes, with the words a person reads them by. */
export const UQC: readonly { code: string; label: string }[] = [
  { code: "NOS", label: "Numbers" },
  { code: "PCS", label: "Pieces" },
  { code: "UNT", label: "Units" },
  { code: "SET", label: "Sets" },
  { code: "PRS", label: "Pairs" },
  { code: "DOZ", label: "Dozens" },
  { code: "BOX", label: "Box" },
  { code: "CTN", label: "Cartons" },
  { code: "PAC", label: "Packs" },
  { code: "BAG", label: "Bags" },
  { code: "BDL", label: "Bundles" },
  { code: "ROL", label: "Rolls" },
  { code: "BTL", label: "Bottles" },
  { code: "CAN", label: "Cans" },
  { code: "DRM", label: "Drums" },
  { code: "TUB", label: "Tubes" },
  { code: "KGS", label: "Kilograms" },
  { code: "GMS", label: "Grams" },
  { code: "QTL", label: "Quintal" },
  { code: "TON", label: "Tonnes" },
  { code: "MTS", label: "Metric ton" },
  { code: "LTR", label: "Litres" },
  { code: "MLT", label: "Millilitres" },
  { code: "KLR", label: "Kilolitre" },
  { code: "MTR", label: "Metres" },
  { code: "CMS", label: "Centimetres" },
  { code: "KME", label: "Kilometre" },
  { code: "YDS", label: "Yards" },
  { code: "SQM", label: "Square metres" },
  { code: "SQF", label: "Square feet" },
  { code: "SQY", label: "Square yards" },
  { code: "CBM", label: "Cubic metres" },
  { code: "CCM", label: "Cubic centimetres" },
  { code: "BAL", label: "Bale" },
  { code: "BKL", label: "Buckles" },
  { code: "BOU", label: "Billion of units" },
  { code: "BUN", label: "Bunches" },
  { code: "GGK", label: "Great gross" },
  { code: "GRS", label: "Gross" },
  { code: "GYD", label: "Gross yards" },
  { code: "TBS", label: "Tablets" },
  { code: "TGM", label: "Ten gross" },
  { code: "THD", label: "Thousands" },
  { code: "UGS", label: "US gallons" },
  { code: "OTH", label: "Others" },
];

export const UQC_OPTIONS = UQC.map((u) => ({ value: u.code, label: `${u.code} — ${u.label}` }));

const UQC_CODES = new Set(UQC.map((u) => u.code));

/** Common inventory unit spellings, mapped to the code a return accepts. */
const INVENTORY_UOM: Readonly<Record<string, string>> = {
  pc: "PCS",
  pcs: "PCS",
  piece: "PCS",
  pieces: "PCS",
  no: "NOS",
  nos: "NOS",
  ea: "NOS",
  each: "NOS",
  unit: "UNT",
  units: "UNT",
  kg: "KGS",
  kgs: "KGS",
  g: "GMS",
  gm: "GMS",
  gram: "GMS",
  t: "TON",
  ton: "TON",
  tonne: "TON",
  l: "LTR",
  ltr: "LTR",
  litre: "LTR",
  ml: "MLT",
  m: "MTR",
  mtr: "MTR",
  metre: "MTR",
  meter: "MTR",
  cm: "CMS",
  km: "KME",
  sqm: "SQM",
  m2: "SQM",
  sqft: "SQF",
  ft2: "SQF",
  m3: "CBM",
  cbm: "CBM",
  box: "BOX",
  ctn: "CTN",
  carton: "CTN",
  pack: "PAC",
  pkt: "PAC",
  bag: "BAG",
  roll: "ROL",
  set: "SET",
  pair: "PRS",
  dozen: "DOZ",
  doz: "DOZ",
  bottle: "BTL",
};

/** An inventory unit (`kg`, `pcs`, `box`) as a GST unit code, or null when it has no clear one. */
export function uqcForInventoryUnit(uom: string): string | null {
  const key = uom.trim().toLowerCase();
  if (key === "") return null;
  const upper = key.toUpperCase();
  if (UQC_CODES.has(upper)) return upper;
  return INVENTORY_UOM[key] ?? null;
}

/** What each missing supplier fact is called on screen. */
export const SUPPLIER_FIELD_LABEL: Record<SupplierField, string> = {
  gst_registration: "GST registration (GSTIN and legal name)",
  legal_name: "Legal name",
  address_line1: "Registered address",
  city: "City",
  pincode: "PIN code",
};
