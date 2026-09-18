/**
 * Realistic fixture data, shaped exactly like the API responses the storefront service is
 * specified to return, so the whole app renders without a live login. Swapped out by the
 * `USE_FIXTURES` flag in `client.ts`.
 *
 * **Two catalogues, keyed by store mode.** A `trade` store sells `FST-HX-M12 Hex bolt M12 × 60`;
 * a `retail` store sells `Amul Gold Full Cream Milk`. One catalogue serving both would put a
 * "27% OFF" flag on a hex bolt, which is indefensible in front of a merchant. `client.ts` picks
 * the slice matching the adopted store's mode, and only in fixture mode — a live server returns
 * the merchant's own catalogue and has no business knowing about either of these.
 *
 * `PRODUCTS` / `CATEGORY_LIST` are the trade slice: a believable Indian B2B catalogue of 24 lines
 * across ten categories, each with a part code, a selling unit, a price band in integer minor
 * units (paise), an MOQ and a stock/lead-time fact. Those three facts — code, unit, availability —
 * are what a dealer actually reads, so every commerce section renders them.
 *
 * `RETAIL_PRODUCTS` / `RETAIL_CATEGORY_LIST` are the retail slice: a quick-commerce grocery aisle
 * and a furniture showroom, with MRPs, delivery promises and pack-size variants.
 */

import type { Category, Product, ProductVariant } from "./types";
import type { RetailCategoryKey } from "@/theme/sources";
import { RETAIL_CATEGORIES, TRADE_CATEGORIES } from "@/theme/sources";

/** Paise, so ₹184.50 is 18450. Integer minor units, platform-wide. */
const rupees = (whole: number): number => whole * 100;

/** ₹27.50 → 2750. Retail prices are authored the way a price tag prints them and stored as the
 *  integer minor unit; nothing downstream ever sees the rupee figure. */
const rs = (amount: number): number => Math.round(amount * 100);

/**
 * The trade lines, minus the four fields whose answer is the same for every one of them.
 *
 * A wholesaler publishes no MRP, promises *dispatch* rather than door-to-door delivery, sells a
 * line in one presentation, and has no bundled sample art. Saying that once is honest; repeating
 * `mrp_minor: null` twenty-four times invites someone to "fill one in" and put a discount flag on
 * a structural beam.
 */
type TradeLine = Omit<Product, "image_url" | "mrp_minor" | "delivery_eta_minutes" | "variants">;

const TRADE_LINES: TradeLine[] = [
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

/** The trade catalogue. Every line answers the four retail-shaped fields the same way. */
export const PRODUCTS: Product[] = TRADE_LINES.map((line) => ({
  ...line,
  image_url: null,
  mrp_minor: null,
  delivery_eta_minutes: null,
  variants: [],
}));

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

export const CATEGORY_LIST: Category[] = TRADE_CATEGORIES.map((c) => ({
  key: c.key,
  label: c.label,
  blurb: BLURBS[c.key] ?? "",
  line_count: PRODUCTS.filter((p) => p.category === c.key).length,
  // No bundled art for the trade vocabulary — a hex bolt tile drawn flat reads worse than the
  // generated `ImagePlate`, which at least varies per category seed.
  image_url: null,
}));

/* ------------------------------ retail slice ------------------------------ */

/**
 * How a retail line is authored.
 *
 * Shorthand, not a second contract: `retailLine` below expands one of these into a full `Product`.
 * Prices are written in rupees the way a price tag prints them and stored as minor units; every
 * optional field is *omitted* rather than set to `undefined`, which `exactOptionalPropertyTypes`
 * requires, and each omission means something — no `mrp` is a line the merchant publishes no MRP
 * for, no `eta` is a line with no delivery promise.
 */
interface RetailSeed {
  id: string;
  code: string;
  name: string;
  brand: string;
  category: RetailCategoryKey;
  spec: string;
  unit: string;
  /** Selling price in rupees. Ignored when `packs` is given — see `retailLine`. */
  price: number;
  /** Printed MRP in rupees. Omitted for the many lines that simply are not discounted. */
  mrp?: number;
  /** Door-to-door delivery promise in minutes. Omitted where there is none. */
  eta?: number;
  /** File stem under `/img`, e.g. `p-milk`. Omitted lines fall back to the generated plate. */
  img?: string;
  /** Defaults to true. */
  stock?: boolean;
  /** Working days to dispatch. Defaults to 0. */
  lead?: number;
  featured?: boolean;
  /** Pack sizes, smallest first. `packs[0]` is the default pack and the canonical "from" price. */
  packs?: { label: string; price: number; mrp?: number; stock?: boolean }[];
}

/**
 * Expand a seed into a `Product`.
 *
 * When `packs` is present the product-level price and MRP are taken **from `packs[0]`**, not from
 * the seed's own `price`. That is the invariant documented on `Product.variants` enforced at the
 * only place it can be enforced — if the two are ever authored differently, the variant wins and
 * they cannot drift. A rail with no variant selector therefore always prints the default pack's
 * true price.
 */
function retailLine(seed: RetailSeed): Product {
  const variants: ProductVariant[] = (seed.packs ?? []).map((pack, index) => ({
    id: `${seed.id}-v${index + 1}`,
    label: pack.label,
    price_min_minor: rs(pack.price),
    price_max_minor: rs(pack.price),
    mrp_minor: pack.mrp === undefined ? null : rs(pack.mrp),
    in_stock: pack.stock ?? true,
  }));
  const base = variants[0];

  return {
    id: seed.id,
    code: seed.code,
    name: seed.name,
    brand: seed.brand,
    category: seed.category,
    spec: seed.spec,
    unit: seed.unit,
    price_min_minor: base ? base.price_min_minor : rs(seed.price),
    price_max_minor: base ? base.price_max_minor : rs(seed.price),
    currency: "INR",
    // A retail shelf price is what the customer pays. GST is inside it, always.
    gst_inclusive: true,
    moq: 1,
    moq_unit: "pack",
    in_stock: seed.stock ?? true,
    lead_time_days: seed.lead ?? 0,
    featured: seed.featured ?? false,
    image_url: seed.img === undefined ? null : `/img/${seed.img}.svg`,
    mrp_minor: base ? base.mrp_minor : seed.mrp === undefined ? null : rs(seed.mrp),
    delivery_eta_minutes: seed.eta === undefined ? null : seed.eta,
    variants,
  };
}

/**
 * The retail catalogue: a quick-commerce grocery aisle and a furniture showroom.
 *
 * Deliberately lumpy. Categories differ in size because real ones do, most lines carry **no MRP at
 * all** because a store where every item is discounted reads as fake, delivery promises range from
 * 8 to 16 minutes, and the furniture lines carry **no `delivery_eta_minutes` whatsoever** — a
 * showroom promises a delivery *window* in days, and printing "8 mins" against a king bed would be
 * the exact lie `delivery_eta_minutes` exists to avoid.
 */
const RETAIL_SEEDS: RetailSeed[] = [
  /* -- staples -- */
  { id: "ret-1001", code: "GRO-ATT-AAS", name: "Aashirvaad Shudh Chakki Atta", brand: "Aashirvaad",
    category: "staples", spec: "100% whole wheat, chakki ground, no maida", unit: "per pack",
    price: 265, eta: 11, img: "p-atta", featured: true,
    packs: [{ label: "5 kg", price: 265, mrp: 310 }, { label: "10 kg", price: 525, mrp: 610 }] },
  { id: "ret-1002", code: "GRO-RIC-IGC", name: "India Gate Classic Basmati Rice", brand: "India Gate",
    category: "staples", spec: "Two-year aged long grain, extra-long after cooking", unit: "per pack",
    price: 138, eta: 12, img: "p-rice", featured: true,
    packs: [{ label: "1 kg", price: 138, mrp: 152 }, { label: "5 kg", price: 649, mrp: 720 }] },
  { id: "ret-1003", code: "GRO-DAL-TSP", name: "Tata Sampann Unpolished Toor Dal", brand: "Tata Sampann",
    category: "staples", spec: "Unpolished, no added oil or water polish", unit: "1 kg pack",
    price: 184, mrp: 210, eta: 13, img: "p-dal" },
  { id: "ret-1004", code: "GRO-OIL-FSL", name: "Fortune Sunlite Refined Sunflower Oil", brand: "Fortune",
    category: "staples", spec: "Refined sunflower oil, light and vitamin fortified", unit: "per pack",
    price: 139, eta: 12, img: "p-oil", featured: true,
    packs: [{ label: "1 L", price: 139, mrp: 165 }, { label: "5 L", price: 679, mrp: 790 }] },
  { id: "ret-1005", code: "GRO-SLT-TAT", name: "Tata Salt Iodised", brand: "Tata",
    category: "staples", spec: "Vacuum evaporated, iodised, free flowing", unit: "1 kg pack",
    price: 28, mrp: 30, eta: 9 },
  { id: "ret-1006", code: "GRO-BSN-RAJ", name: "Rajdhani Besan", brand: "Rajdhani",
    category: "staples", spec: "Fine ground chana dal flour", unit: "500 g pack", price: 72, eta: 14 },
  { id: "ret-1007", code: "GRO-ATT-PAT", name: "Patanjali Chakki Fresh Atta", brand: "Patanjali",
    category: "staples", spec: "Whole wheat, stone ground", unit: "5 kg pack",
    price: 240, mrp: 260, eta: 16 },
  { id: "ret-1008", code: "GRO-RIC-24M", name: "24 Mantra Organic Sona Masuri Rice", brand: "24 Mantra Organic",
    category: "staples", spec: "Certified organic, single origin, medium grain", unit: "5 kg pack",
    price: 520, stock: false, lead: 2 },

  /* -- dairy, bread and eggs -- */
  { id: "ret-1101", code: "GRO-MLK-AGO", name: "Amul Gold Full Cream Milk", brand: "Amul",
    category: "dairy", spec: "6% fat homogenised full cream milk, pasteurised", unit: "per pouch",
    price: 35, eta: 8, img: "p-milk", featured: true,
    packs: [{ label: "500 ml", price: 35 }, { label: "1 L", price: 69 }] },
  { id: "ret-1102", code: "GRO-MLK-ATA", name: "Amul Taaza Toned Milk", brand: "Amul",
    category: "dairy", spec: "3% fat toned milk, pasteurised", unit: "1 L pouch", price: 75, eta: 8 },
  { id: "ret-1103", code: "GRO-DHI-MDC", name: "Mother Dairy Classic Dahi", brand: "Mother Dairy",
    category: "dairy", spec: "Set curd, thick and mildly sour", unit: "per cup",
    price: 45, eta: 9, img: "p-curd",
    packs: [{ label: "400 g", price: 45, mrp: 50 }, { label: "1 kg", price: 92, mrp: 100 }] },
  { id: "ret-1104", code: "GRO-PNR-AML", name: "Amul Malai Paneer", brand: "Amul",
    category: "dairy", spec: "Fresh malai paneer, vacuum packed", unit: "200 g block",
    price: 95, mrp: 99, eta: 10, img: "p-paneer", featured: true },
  { id: "ret-1105", code: "GRO-BRD-BRB", name: "Britannia Brown Bread", brand: "Britannia",
    category: "dairy", spec: "Whole wheat flour, baked daily", unit: "400 g loaf",
    price: 50, eta: 8, img: "p-bread" },
  { id: "ret-1106", code: "GRO-BRD-HGS", name: "Harvest Gold Sandwich White Bread", brand: "Harvest Gold",
    category: "dairy", spec: "Soft crumb, thick slice, no added trans fat", unit: "350 g loaf",
    price: 42, eta: 9 },
  { id: "ret-1107", code: "GRO-BTR-AML", name: "Amul Butter", brand: "Amul",
    category: "dairy", spec: "Pasteurised salted table butter", unit: "per pack",
    price: 62, eta: 10,
    packs: [{ label: "100 g", price: 62, mrp: 64 }, { label: "500 g", price: 285, mrp: 295 }] },
  { id: "ret-1108", code: "GRO-EGG-EGZ", name: "Eggoz Nutrition Brown Eggs", brand: "Eggoz",
    category: "dairy", spec: "Farm fresh brown eggs, antibiotic residue free", unit: "6 pieces",
    price: 89, mrp: 99, eta: 12, img: "p-eggs" },
  { id: "ret-1109", code: "GRO-GHE-AML", name: "Amul Pure Ghee", brand: "Amul",
    category: "dairy", spec: "Made from cow and buffalo milk, granular texture", unit: "per tin",
    price: 315, eta: 14, img: "p-ghee", featured: true,
    packs: [{ label: "500 ml", price: 315, mrp: 350 }, { label: "1 L", price: 610, mrp: 680 }] },
  { id: "ret-1110", code: "GRO-MLK-NSA", name: "Nestlé a+ Slim Toned Milk", brand: "Nestlé",
    category: "dairy", spec: "Tetra pack toned milk, six month shelf life", unit: "1 L carton",
    price: 78, stock: false, lead: 1 },

  /* -- snacks and namkeen -- */
  { id: "ret-1201", code: "GRO-CHP-LAY", name: "Lay's India's Magic Masala", brand: "Lay's",
    category: "snacks", spec: "Potato chips, masala seasoning", unit: "52 g packet",
    price: 20, eta: 8, img: "p-chips", featured: true },
  { id: "ret-1202", code: "GRO-NAM-HAB", name: "Haldiram's Aloo Bhujia", brand: "Haldiram's",
    category: "snacks", spec: "Potato and gram flour namkeen, medium spice", unit: "per pack",
    price: 52, eta: 9, img: "p-namkeen",
    packs: [{ label: "200 g", price: 52, mrp: 55 }, { label: "400 g", price: 99, mrp: 110 }] },
  { id: "ret-1203", code: "GRO-BIS-PGO", name: "Parle-G Original Glucose Biscuits", brand: "Parle",
    category: "snacks", spec: "Glucose biscuits, family pack", unit: "800 g pack",
    price: 90, mrp: 100, eta: 10, img: "p-biscuits" },
  { id: "ret-1204", code: "GRO-BIS-BGD", name: "Britannia Good Day Cashew Cookies", brand: "Britannia",
    category: "snacks", spec: "Butter cookies with cashew pieces", unit: "600 g pack",
    price: 110, mrp: 125, eta: 11 },
  { id: "ret-1205", code: "GRO-CHO-CDS", name: "Cadbury Dairy Milk Silk", brand: "Cadbury",
    category: "snacks", spec: "Smooth milk chocolate bar", unit: "150 g bar",
    price: 175, mrp: 190, eta: 9, img: "p-chocolate", featured: true },
  { id: "ret-1206", code: "GRO-CHP-BMA", name: "Bingo! Mad Angles Achaari Masti", brand: "Bingo!",
    category: "snacks", spec: "Triangular corn snack, achaari seasoning", unit: "66 g packet",
    price: 20, eta: 8 },
  { id: "ret-1207", code: "GRO-SWT-HSP", name: "Haldiram's Soan Papdi", brand: "Haldiram's",
    category: "snacks", spec: "Flaky gram flour sweet with cardamom", unit: "250 g box",
    price: 95, eta: 13 },
  { id: "ret-1208", code: "GRO-CHP-TYM", name: "Too Yumm! Multigrain Chips", brand: "Too Yumm!",
    category: "snacks", spec: "Baked multigrain chips, no palm oil", unit: "55 g packet",
    price: 35, stock: false, lead: 2 },

  /* -- tea, coffee and drinks -- */
  { id: "ret-1301", code: "GRO-TEA-TTG", name: "Tata Tea Gold", brand: "Tata Tea",
    category: "beverages", spec: "Assam leaf blend with 15% long leaves", unit: "per pack",
    price: 148, eta: 10, img: "p-tea", featured: true,
    packs: [{ label: "250 g", price: 148, mrp: 160 }, { label: "500 g", price: 285, mrp: 315 }] },
  { id: "ret-1302", code: "GRO-COF-NSC", name: "Nescafé Classic Instant Coffee", brand: "Nescafé",
    category: "beverages", spec: "100% pure soluble coffee, dark roast", unit: "50 g jar",
    price: 230, mrp: 250, eta: 11, img: "p-coffee" },
  { id: "ret-1303", code: "GRO-COF-BRG", name: "Bru Gold Instant Coffee", brand: "Bru",
    category: "beverages", spec: "Freeze dried granules, medium roast", unit: "100 g jar",
    price: 355, eta: 12 },
  { id: "ret-1304", code: "GRO-CLD-COC", name: "Coca-Cola", brand: "Coca-Cola",
    category: "beverages", spec: "Carbonated soft drink, chilled on despatch", unit: "750 ml bottle",
    price: 40, eta: 8, img: "p-cola" },
  { id: "ret-1305", code: "GRO-JUC-RMF", name: "Real Mixed Fruit Juice", brand: "Real",
    category: "beverages", spec: "Fruit juice from concentrate, no added preservatives", unit: "1 L carton",
    price: 110, mrp: 125, eta: 9, img: "p-juice" },
  { id: "ret-1306", code: "GRO-JUC-PBA", name: "Paper Boat Aam Panna", brand: "Paper Boat",
    category: "beverages", spec: "Raw mango drink with roasted cumin", unit: "250 ml bottle",
    price: 35, eta: 10 },
  { id: "ret-1307", code: "GRO-TEA-RLN", name: "Red Label Natural Care Tea", brand: "Brooke Bond",
    category: "beverages", spec: "Black tea with five ayurvedic ingredients", unit: "250 g pack",
    price: 150, mrp: 160, eta: 12 },
  { id: "ret-1308", code: "GRO-CLD-SPR", name: "Sprite", brand: "Sprite",
    category: "beverages", spec: "Lemon-lime carbonated soft drink", unit: "2 L bottle",
    price: 95, stock: false, lead: 1 },

  /* -- fruits and vegetables -- */
  { id: "ret-1401", code: "GRO-VEG-TOM", name: "Tomato, Hybrid", brand: "Local Farms",
    category: "fresh", spec: "Firm salad grade, sorted same morning", unit: "per pack",
    price: 24, eta: 8, img: "p-tomato", featured: true,
    packs: [{ label: "500 g", price: 24 }, { label: "1 kg", price: 45 }] },
  { id: "ret-1402", code: "GRO-VEG-ONI", name: "Onion", brand: "Local Farms",
    category: "fresh", spec: "Nashik red onion, medium size", unit: "1 kg pack",
    price: 38, eta: 9, img: "p-onion" },
  { id: "ret-1403", code: "GRO-FRT-BAN", name: "Banana, Robusta", brand: "Local Farms",
    category: "fresh", spec: "Ripened naturally, no carbide", unit: "6 pieces",
    price: 42, eta: 8, img: "p-banana" },
  { id: "ret-1404", code: "GRO-VEG-POT", name: "Potato", brand: "Local Farms",
    category: "fresh", spec: "Fresh table potato, washed", unit: "1 kg pack", price: 32, eta: 9 },
  { id: "ret-1405", code: "GRO-VEG-COR", name: "Coriander Leaves", brand: "Local Farms",
    category: "fresh", spec: "Fresh bunch, roots trimmed", unit: "100 g bunch", price: 15, eta: 10 },
  { id: "ret-1406", code: "GRO-FRT-APL", name: "Shimla Apple", brand: "Local Farms",
    category: "fresh", spec: "Royal delicious, hand picked", unit: "4 pieces",
    price: 152, mrp: 180, eta: 12 },
  { id: "ret-1407", code: "GRO-FRT-ORG", name: "Nagpur Orange", brand: "Local Farms",
    category: "fresh", spec: "Loose skin, seasonal harvest", unit: "1 kg pack", price: 96, eta: 11 },
  { id: "ret-1408", code: "GRO-VEG-CAP", name: "Green Capsicum", brand: "Local Farms",
    category: "fresh", spec: "Thick walled, salad grade", unit: "250 g pack",
    price: 22, stock: false, lead: 1 },

  /* -- cleaning and household -- */
  { id: "ret-1501", code: "GRO-DET-SEM", name: "Surf Excel Matic Top Load Detergent Powder", brand: "Surf Excel",
    category: "household", spec: "For top load machines, low foam formula", unit: "per pack",
    price: 239, eta: 14, img: "p-detergent", featured: true,
    packs: [{ label: "1 kg", price: 239, mrp: 270 }, { label: "2 kg", price: 460, mrp: 525 }] },
  { id: "ret-1502", code: "GRO-CLN-LZC", name: "Lizol Disinfectant Floor Cleaner, Citrus", brand: "Lizol",
    category: "household", spec: "Kills 99.9% of germs, safe on all floors", unit: "975 ml bottle",
    price: 199, mrp: 235, eta: 13, img: "p-floorcleaner" },
  { id: "ret-1503", code: "GRO-CLN-VIM", name: "Vim Dishwash Bar", brand: "Vim",
    category: "household", spec: "Lemon bar, cuts grease in one wipe", unit: "5 × 150 g",
    price: 60, mrp: 65, eta: 12 },
  { id: "ret-1504", code: "GRO-CLN-HRP", name: "Harpic Power Plus Toilet Cleaner", brand: "Harpic",
    category: "household", spec: "Thick liquid, original formulation", unit: "1 L bottle",
    price: 185, mrp: 210, eta: 13 },
  { id: "ret-1505", code: "GRO-CLN-COL", name: "Colin Glass Cleaner", brand: "Colin",
    category: "household", spec: "Streak free shine, trigger spray", unit: "500 ml spray",
    price: 99, eta: 14 },
  { id: "ret-1506", code: "GRO-CLN-SBS", name: "Scotch-Brite Scrub Pad", brand: "Scotch-Brite",
    category: "household", spec: "Anti-bacterial scrub pad, medium size", unit: "3 pieces",
    price: 55, eta: 15 },

  /* -- sofas and seating -- */
  { id: "ret-2001", code: "HOM-SOF-KWT", name: "Kashmir Walnut Three-Seater Sofa", brand: "AGM Handicrafts",
    category: "seating", spec: "Hand-carved walnut frame, linen upholstery, 198 cm", unit: "each",
    price: 86900, mrp: 112000, img: "p-sofa", featured: true, stock: false, lead: 21 },
  { id: "ret-2002", code: "HOM-CHR-JSW", name: "Jodhpur Sheesham Wingback Armchair", brand: "AGM Handicrafts",
    category: "seating", spec: "Solid sheesham, hand-tufted velvet seat", unit: "each",
    price: 24500, mrp: 29900, img: "p-armchair", stock: false, lead: 14 },
  { id: "ret-2003", code: "HOM-CHR-CTA", name: "Chettinad Teak Accent Chair", brand: "AGM Handicrafts",
    category: "seating", spec: "Reclaimed teak, woven cane back", unit: "each", price: 18900, lead: 0 },
  { id: "ret-2004", code: "HOM-BEN-KSB", name: "Kanchipuram Silk Upholstered Bench", brand: "AGM Handicrafts",
    category: "seating", spec: "Mango wood frame, handloom silk top, 120 cm", unit: "each",
    price: 15400, mrp: 18000, stock: false, lead: 25 },

  /* -- dining and tables -- */
  { id: "ret-2101", code: "HOM-TBL-RSD", name: "Rosewood Six-Seater Dining Table", brand: "AGM Handicrafts",
    category: "dining", spec: "Solid rosewood top, 180 × 90 cm, chairs sold separately", unit: "each",
    price: 94000, mrp: 118000, img: "p-diningtable", featured: true, stock: false, lead: 28 },
  { id: "ret-2102", code: "HOM-TBL-SNC", name: "Sheesham Nesting Coffee Table, set of two", brand: "AGM Handicrafts",
    category: "dining", spec: "Hand-planed sheesham, brass inlay edge", unit: "set of 2",
    price: 21800, img: "p-coffeetable", stock: false, lead: 12 },
  { id: "ret-2103", code: "HOM-TBL-MCT", name: "Marble-Top Console Table", brand: "AGM Handicrafts",
    category: "dining", spec: "Makrana marble top on a blackened iron base, 110 cm", unit: "each",
    price: 34500, mrp: 39000, stock: false, lead: 20 },

  /* -- beds and wardrobes -- */
  { id: "ret-2201", code: "HOM-BED-TKS", name: "Teak King Bed with Storage", brand: "AGM Handicrafts",
    category: "beds", spec: "Solid teak, hydraulic storage, king size", unit: "each",
    price: 124000, mrp: 152000, img: "p-bed", featured: true, stock: false, lead: 30 },
  { id: "ret-2202", code: "HOM-WRD-SFD", name: "Sheesham Four-Door Wardrobe", brand: "AGM Handicrafts",
    category: "beds", spec: "Four doors, internal drawers, hand-finished brass pulls", unit: "each",
    price: 78500, img: "p-wardrobe", stock: false, lead: 26 },
  { id: "ret-2203", code: "HOM-SHF-SOB", name: "Sheesham Open Bookshelf, five tier", brand: "AGM Handicrafts",
    category: "beds", spec: "Open-back five tier shelf, 180 cm tall", unit: "each",
    price: 32600, mrp: 38000, img: "p-bookshelf", stock: false, lead: 18 },
  { id: "ret-2204", code: "HOM-BST-MWB", name: "Mango Wood Bedside Table", brand: "AGM Handicrafts",
    category: "beds", spec: "Two drawers, hand-turned legs", unit: "each",
    price: 9800, mrp: 11500, lead: 0 },

  /* -- lighting and décor -- */
  { id: "ret-2301", code: "HOM-LGT-HBP", name: "Hammered Brass Pendant Light", brand: "AGM Handicrafts",
    category: "lighting", spec: "Hand-hammered brass shade, 40 cm, E27 fitting", unit: "each",
    price: 8400, mrp: 9900, img: "p-pendantlamp", featured: true, lead: 7, stock: false },
  { id: "ret-2302", code: "HOM-LGT-TTF", name: "Teak Tripod Floor Lamp", brand: "AGM Handicrafts",
    category: "lighting", spec: "Teak tripod with handwoven cotton shade, 150 cm", unit: "each",
    price: 14200, img: "p-floorlamp", stock: false, lead: 12 },
  { id: "ret-2303", code: "HOM-LGT-BPT", name: "Blue Pottery Table Lamp", brand: "AGM Handicrafts",
    category: "lighting", spec: "Jaipur blue pottery base, cotton drum shade", unit: "each",
    price: 4600, lead: 0 },
];

export const RETAIL_PRODUCTS: Product[] = RETAIL_SEEDS.map(retailLine);

const RETAIL_BLURBS: Record<string, string> = {
  staples: "Atta, rice, dal and oil from the brands your kitchen already runs on.",
  dairy: "Milk, curd, paneer and bread, picked and packed the morning they reach you.",
  snacks: "Chips, namkeen, biscuits and chocolate for the shelf that empties fastest.",
  beverages: "Chai, filter coffee and cold drinks, delivered cold.",
  fresh: "Fruit and vegetables sorted the same morning, never a day older.",
  household: "Detergent, floor cleaner and everything the weekend clean needs.",
  seating: "Hand-carved sofas, armchairs and benches, made to order in solid wood.",
  dining: "Dining tables, consoles and coffee tables in rosewood, teak and marble.",
  beds: "Beds, wardrobes and shelving built from a single timber and finished by hand.",
  lighting: "Brass, teak and blue pottery lighting, lit for the room it stands in.",
};

/** File stem of the bundled tile drawn for each retail category. */
const RETAIL_CATEGORY_ART: Record<string, string> = {
  staples: "cat-staples",
  dairy: "cat-dairy",
  snacks: "cat-snacks",
  beverages: "cat-beverages",
  fresh: "cat-fresh",
  household: "cat-household",
  seating: "cat-seating",
  dining: "cat-dining",
  beds: "cat-beds",
  lighting: "cat-lighting",
};

export const RETAIL_CATEGORY_LIST: Category[] = RETAIL_CATEGORIES.map((c) => {
  const art = RETAIL_CATEGORY_ART[c.key];
  return {
    key: c.key,
    label: c.label,
    blurb: RETAIL_BLURBS[c.key] ?? "",
    line_count: RETAIL_PRODUCTS.filter((p) => p.category === c.key).length,
    image_url: art === undefined ? null : `/img/${art}.svg`,
  };
});
