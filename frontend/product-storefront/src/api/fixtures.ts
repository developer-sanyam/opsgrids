/**
 * Realistic fixture data, shaped exactly like the API responses the storefront service is
 * specified to return, so the whole app renders without a live login. Swapped out by the
 * `USE_FIXTURES` flag in `client.ts`.
 *
 * A believable Indian B2B catalogue: 24 lines across ten categories, each with a part code, a
 * selling unit, a price band in integer minor units (paise), an MOQ and a stock/lead-time fact.
 * These three facts — code, unit, availability — are what a dealer actually reads, so every
 * commerce section renders them.
 */

import type { Category, Product } from "./types";
import { CATEGORIES } from "@/theme/sources";

/** Paise, so ₹184.50 is 18450. Integer minor units, platform-wide. */
const rupees = (whole: number): number => whole * 100;

export const PRODUCTS: Product[] = [
  {
    id: "prd-0001", code: "FST-HX-M12", name: "Hex bolt M12 × 60", brand: "Unbrako",
    category: "fasteners", spec: "Grade 8.8, hot-dip galvanised, full thread", unit: "per 100 pcs",
    price_min_minor: rupees(1240), price_max_minor: rupees(1480), currency: "INR",
    gst_inclusive: false, moq: 500, moq_unit: "pcs", in_stock: true, lead_time_days: 0, featured: true,
  },
  {
    id: "prd-0002", code: "FST-NYL-M10", name: "Nyloc nut M10", brand: "Unbrako",
    category: "fasteners", spec: "Class 8, zinc plated, DIN 985", unit: "per 100 pcs",
    price_min_minor: rupees(310), price_max_minor: rupees(340), currency: "INR",
    gst_inclusive: false, moq: 1000, moq_unit: "pcs", in_stock: true, lead_time_days: 0, featured: false,
  },
  {
    id: "prd-0003", code: "FST-ANC-12", name: "Wedge anchor 12 × 100", brand: "Fischer",
    category: "fasteners", spec: "Zinc plated, ETA option 7, for cracked concrete", unit: "per 50 pcs",
    price_min_minor: rupees(2150), price_max_minor: rupees(2150), currency: "INR",
    gst_inclusive: false, moq: 200, moq_unit: "pcs", in_stock: false, lead_time_days: 7, featured: false,
  },
  {
    id: "prd-0004", code: "BRG-6205-2RS", name: "Deep groove ball bearing 6205-2RS", brand: "SKF",
    category: "bearings", spec: "25 × 52 × 15mm, contact seals both sides", unit: "per pc",
    price_min_minor: rupees(286), price_max_minor: rupees(340), currency: "INR",
    gst_inclusive: false, moq: 25, moq_unit: "pcs", in_stock: true, lead_time_days: 0, featured: true,
  },
  {
    id: "prd-0005", code: "BRG-UCP-210", name: "Pillow block UCP 210", brand: "NBC",
    category: "bearings", spec: "50mm bore, cast iron housing, grease nipple", unit: "per pc",
    price_min_minor: rupees(940), price_max_minor: rupees(1120), currency: "INR",
    gst_inclusive: false, moq: 10, moq_unit: "pcs", in_stock: true, lead_time_days: 0, featured: false,
  },
  {
    id: "prd-0006", code: "BRG-VB-B80", name: "V-belt B80 wrapped", brand: "Fenner",
    category: "bearings", spec: "17 × 11mm section, 2032mm outside length", unit: "per pc",
    price_min_minor: rupees(418), price_max_minor: rupees(418), currency: "INR",
    gst_inclusive: false, moq: 20, moq_unit: "pcs", in_stock: false, lead_time_days: 4, featured: false,
  },
  {
    id: "prd-0007", code: "CBL-FRLS-2.5", name: "FRLS single core cable 2.5 sq mm", brand: "Polycab",
    category: "cables", spec: "1100V, 90m coil, flame retardant low smoke", unit: "per 90m coil",
    price_min_minor: rupees(2340), price_max_minor: rupees(2680), currency: "INR",
    gst_inclusive: false, moq: 10, moq_unit: "coils", in_stock: true, lead_time_days: 0, featured: true,
  },
  {
    id: "prd-0008", code: "CBL-ARM-4C6", name: "Armoured cable 4C × 6 sq mm", brand: "Havells",
    category: "cables", spec: "XLPE, aluminium conductor, steel wire armour", unit: "per metre",
    price_min_minor: rupees(214), price_max_minor: rupees(268), currency: "INR",
    gst_inclusive: false, moq: 100, moq_unit: "metres", in_stock: true, lead_time_days: 0, featured: false,
  },
  {
    id: "prd-0009", code: "CBL-GLD-32", name: "Brass cable gland 32mm", brand: "Comet",
    category: "cables", spec: "Double compression, IP68, with earth tag", unit: "per 25 pcs",
    price_min_minor: rupees(3150), price_max_minor: rupees(3150), currency: "INR",
    gst_inclusive: false, moq: 50, moq_unit: "pcs", in_stock: false, lead_time_days: 5, featured: false,
  },
  {
    id: "prd-0010", code: "SWG-MCB-C32", name: "MCB 32A C-curve, single pole", brand: "Schneider",
    category: "switchgear", spec: "10kA breaking capacity, 240V AC", unit: "per pc",
    price_min_minor: rupees(268), price_max_minor: rupees(312), currency: "INR",
    gst_inclusive: false, moq: 60, moq_unit: "pcs", in_stock: true, lead_time_days: 0, featured: true,
  },
  {
    id: "prd-0011", code: "SWG-CON-25", name: "Power contactor 25A, 3 pole", brand: "L&T",
    category: "switchgear", spec: "AC-3 duty, 415V coil, 1NO auxiliary", unit: "per pc",
    price_min_minor: rupees(1640), price_max_minor: rupees(1980), currency: "INR",
    gst_inclusive: false, moq: 10, moq_unit: "pcs", in_stock: true, lead_time_days: 0, featured: false,
  },
  {
    id: "prd-0012", code: "SWG-DB-12W", name: "Distribution board, 12 way TPN", brand: "Hager",
    category: "switchgear", spec: "IP43 metal enclosure, double door, with busbar", unit: "per pc",
    price_min_minor: rupees(4280), price_max_minor: rupees(4980), currency: "INR",
    gst_inclusive: false, moq: 5, moq_unit: "pcs", in_stock: false, lead_time_days: 10, featured: false,
  },
  {
    id: "prd-0013", code: "CEM-OPC-53", name: "OPC 53 grade cement", brand: "UltraTech",
    category: "cement", spec: "IS 269:2015, 50kg HDPE bag, batch certified", unit: "per 50kg bag",
    price_min_minor: rupees(396), price_max_minor: rupees(442), currency: "INR",
    gst_inclusive: false, moq: 200, moq_unit: "bags", in_stock: true, lead_time_days: 0, featured: true,
  },
  {
    id: "prd-0014", code: "CEM-PPC-FA", name: "PPC fly-ash blended cement", brand: "ACC",
    category: "cement", spec: "IS 1489 Part 1, 50kg bag, low heat of hydration", unit: "per 50kg bag",
    price_min_minor: rupees(362), price_max_minor: rupees(404), currency: "INR",
    gst_inclusive: false, moq: 200, moq_unit: "bags", in_stock: true, lead_time_days: 0, featured: false,
  },
  {
    id: "prd-0015", code: "CEM-WPF-20", name: "Integral waterproofing compound", brand: "Dr. Fixit",
    category: "cement", spec: "20 litre can, dosage 200ml per 50kg cement", unit: "per 20L can",
    price_min_minor: rupees(2860), price_max_minor: rupees(2860), currency: "INR",
    gst_inclusive: false, moq: 20, moq_unit: "cans", in_stock: true, lead_time_days: 0, featured: false,
  },
  {
    id: "prd-0016", code: "STL-TMT-16", name: "TMT rebar Fe 550D, 16mm", brand: "Tata Tiscon",
    category: "steel", spec: "12m length, IS 1786, mill test certificate", unit: "per tonne",
    price_min_minor: rupees(58400), price_max_minor: rupees(63200), currency: "INR",
    gst_inclusive: false, moq: 2, moq_unit: "tonnes", in_stock: true, lead_time_days: 0, featured: true,
  },
  {
    id: "prd-0017", code: "STL-ISMB-200", name: "ISMB 200 structural beam", brand: "SAIL",
    category: "steel", spec: "200 × 100mm, 24.2 kg/m, 12m standard length", unit: "per tonne",
    price_min_minor: rupees(61800), price_max_minor: rupees(66400), currency: "INR",
    gst_inclusive: false, moq: 3, moq_unit: "tonnes", in_stock: false, lead_time_days: 6, featured: false,
  },
  {
    id: "prd-0018", code: "STL-GI-SHT", name: "GI sheet 1.2mm, 120 GSM", brand: "JSW",
    category: "steel", spec: "1220 × 2440mm, zinc coated, mill finish", unit: "per sheet",
    price_min_minor: rupees(2940), price_max_minor: rupees(3320), currency: "INR",
    gst_inclusive: false, moq: 25, moq_unit: "sheets", in_stock: true, lead_time_days: 0, featured: false,
  },
  {
    id: "prd-0019", code: "PKG-CTN-5PLY", name: "Corrugated carton 5-ply", brand: "In-house",
    category: "packaging", spec: "450 × 300 × 300mm, 7kg bursting strength", unit: "per 50 pcs",
    price_min_minor: rupees(2250), price_max_minor: rupees(2600), currency: "INR",
    gst_inclusive: false, moq: 500, moq_unit: "pcs", in_stock: true, lead_time_days: 0, featured: true,
  },
  {
    id: "prd-0020", code: "PKG-STR-12", name: "PET strapping roll 12mm", brand: "Signode",
    category: "packaging", spec: "1000m roll, 0.6mm thickness, embossed", unit: "per roll",
    price_min_minor: rupees(3480), price_max_minor: rupees(3480), currency: "INR",
    gst_inclusive: false, moq: 10, moq_unit: "rolls", in_stock: true, lead_time_days: 0, featured: false,
  },
  {
    id: "prd-0021", code: "AGR-NPK-19", name: "NPK 19:19:19 water soluble", brand: "Coromandel",
    category: "agri", spec: "25kg bag, fully water soluble, fertigation grade", unit: "per 25kg bag",
    price_min_minor: rupees(2680), price_max_minor: rupees(2940), currency: "INR",
    gst_inclusive: true, moq: 40, moq_unit: "bags", in_stock: true, lead_time_days: 0, featured: true,
  },
  {
    id: "prd-0022", code: "AGR-DRIP-16", name: "Inline drip lateral 16mm", brand: "Jain Irrigation",
    category: "agri", spec: "2 LPH emitters at 40cm spacing, 400m coil", unit: "per 400m coil",
    price_min_minor: rupees(4120), price_max_minor: rupees(4620), currency: "INR",
    gst_inclusive: true, moq: 20, moq_unit: "coils", in_stock: false, lead_time_days: 8, featured: false,
  },
  {
    id: "prd-0023", code: "TLS-CUT-14", name: "Abrasive cut-off wheel 14 inch", brand: "Bosch",
    category: "tools", spec: "355 × 3 × 25.4mm, for mild steel, A30", unit: "per 25 pcs",
    price_min_minor: rupees(3750), price_max_minor: rupees(4100), currency: "INR",
    gst_inclusive: false, moq: 50, moq_unit: "pcs", in_stock: true, lead_time_days: 0, featured: false,
  },
  {
    id: "prd-0024", code: "SAF-HLM-VT", name: "Safety helmet, ratchet vented", brand: "Karam",
    category: "safety", spec: "IS 2925 certified, HDPE shell, chin strap", unit: "per 20 pcs",
    price_min_minor: rupees(4400), price_max_minor: rupees(4900), currency: "INR",
    gst_inclusive: false, moq: 100, moq_unit: "pcs", in_stock: true, lead_time_days: 0, featured: false,
  },
];

const BLURBS: Record<string, string> = {
  fasteners: "Bolts, nuts, anchors and fixings in grade and finish combinations we stock deep.",
  bearings: "Bearings, housings and belts for maintenance schedules that cannot slip.",
  cables: "FRLS, armoured and control cable off the coil, cut and tagged to your length.",
  switchgear: "Breakers, contactors and boards from the panel builders' standard list.",
  cement: "Cement, admixtures and waterproofing, batch certified and site delivered.",
  steel: "Rebar, sections and sheet against mill test certificates.",
  packaging: "Cartons, strapping and consumables for a despatch bay that never stops.",
  agri: "Nutrition and irrigation inputs for dealers and farmer producer organisations.",
  tools: "Consumable tooling and abrasives sized for a shop floor, not a hardware aisle.",
  safety: "PPE to IS specification, stocked in the sizes crews actually ask for.",
};

export const CATEGORY_LIST: Category[] = CATEGORIES.map((c) => ({
  key: c.key,
  label: c.label,
  blurb: BLURBS[c.key] ?? "",
  line_count: PRODUCTS.filter((p) => p.category === c.key).length,
}));
