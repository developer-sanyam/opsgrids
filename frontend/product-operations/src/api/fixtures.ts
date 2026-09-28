/**
 * Realistic fixture data, shaped exactly like the real API responses so the whole UI renders
 * without a live login. Swapped out by the `USE_FIXTURES` flag in `client.ts`.
 *
 * All money is **integer minor units** (paise) of INR. The numbers are internally consistent:
 * a purchase order's total is the sum of its line items, supplier spend tracks their PO history,
 * and inventory value follows on-hand × unit value — so every rollup the dashboard derives ties
 * back to a row you can find on a table.
 */

import type {
  Category,
  InventoryItemOut,
  ItemAttributeOut,
  ItemCategoryOut,
  ItemVariantOut,
  LocationOut,
  PurchaseOrderOut,
  StockMovementOut,
  SupplierOut,
  SupplierStatus,
  UomOut,
  WarehouseOut,
} from "./types";

/**
 * The reference "today" fixture mode reasons about (delivery lateness, recency) — this seed data
 * was written against it, so it stays internally consistent. Live mode never imports this: it
 * reads the real clock through `lib/now.ts`'s `today()`.
 */
export const FIXTURE_TODAY = "2026-08-24";

const CURRENCY = "INR";

/** ₹ → paise. Keeps the seed readable in rupees while the wire stays in minor units. */
const inr = (rupees: number): number => Math.round(rupees * 100);

/* -------------------------------- suppliers ------------------------------- */

interface SupplierSeed {
  code: string;
  name: string;
  category: Category;
  status: SupplierStatus;
  lead: number;
  onTime: number;
  spend: number; // rupees, lifetime
  openPos: number;
  contact: string;
  email: string;
  location: string;
  since: string;
}

const SUPPLIER_SEEDS: SupplierSeed[] = [
  { code: "SUP-MER", name: "Meridian Metals", category: "raw_materials", status: "active", lead: 12, onTime: 96.4, spend: 41_20_00_000, openPos: 3, contact: "Rajesh Pillai", email: "rajesh@meridianmetals.example", location: "Jamshedpur", since: "2019-04-02" },
  { code: "SUP-IRC", name: "Ironclad Alloys", category: "raw_materials", status: "active", lead: 18, onTime: 91.2, spend: 33_80_00_000, openPos: 2, contact: "Neha Kulkarni", email: "neha@ironcladalloys.example", location: "Raipur", since: "2020-08-11" },
  { code: "SUP-APX", name: "Apex Packaging", category: "packaging", status: "active", lead: 6, onTime: 98.1, spend: 8_64_00_000, openPos: 1, contact: "Farhan Qureshi", email: "farhan@apexpack.example", location: "Vapi", since: "2021-01-19" },
  { code: "SUP-VLT", name: "Volt Components", category: "components", status: "active", lead: 21, onTime: 88.7, spend: 19_45_00_000, openPos: 4, contact: "Karthik Iyer", email: "karthik@voltcomponents.example", location: "Bengaluru", since: "2020-03-27" },
  { code: "SUP-NMB", name: "Nimbus Fasteners", category: "components", status: "on_hold", lead: 15, onTime: 79.5, spend: 6_12_00_000, openPos: 0, contact: "Sunita Rao", email: "sunita@nimbusfast.example", location: "Ludhiana", since: "2022-06-05" },
  { code: "SUP-STL", name: "Sterling MRO Supplies", category: "mro", status: "active", lead: 4, onTime: 94.9, spend: 3_28_00_000, openPos: 2, contact: "Amit Deshmukh", email: "amit@sterlingmro.example", location: "Pune", since: "2021-11-14" },
  { code: "SUP-PLY", name: "Polymer Works", category: "raw_materials", status: "active", lead: 10, onTime: 92.6, spend: 14_90_00_000, openPos: 1, contact: "Divya Menon", email: "divya@polymerworks.example", location: "Ahmedabad", since: "2019-09-30" },
  { code: "SUP-CRL", name: "Crestline Logistics", category: "logistics", status: "active", lead: 3, onTime: 90.3, spend: 5_74_00_000, openPos: 2, contact: "Vikram Sethi", email: "vikram@crestlinelog.example", location: "Mumbai", since: "2020-12-08" },
  { code: "SUP-PRC", name: "Precision Castings Co", category: "components", status: "active", lead: 24, onTime: 85.4, spend: 11_36_00_000, openPos: 1, contact: "Lakshmi Nair", email: "lakshmi@precisioncast.example", location: "Coimbatore", since: "2018-07-22" },
  { code: "SUP-GRL", name: "GreenLeaf Consumables", category: "mro", status: "pending", lead: 8, onTime: 83.0, spend: 92_00_000, openPos: 0, contact: "Tejas Bhatt", email: "tejas@greenleaf.example", location: "Nashik", since: "2024-02-16" },
  { code: "SUP-BRF", name: "BlueRiver Freight", category: "logistics", status: "active", lead: 5, onTime: 87.8, spend: 4_16_00_000, openPos: 1, contact: "Ishita Banerjee", email: "ishita@blueriverfreight.example", location: "Kolkata", since: "2021-05-03" },
  { code: "SUP-ANV", name: "Anvil Industrial Services", category: "services", status: "active", lead: 14, onTime: 89.6, spend: 7_08_00_000, openPos: 0, contact: "Mohammed Sayeed", email: "mohammed@anvilservices.example", location: "Hyderabad", since: "2022-10-01" },
];

const supUuid = (n: number): string =>
  `50110000-0000-4000-8000-${n.toString().padStart(12, "0")}`;

export const SUPPLIERS: SupplierOut[] = SUPPLIER_SEEDS.map((s, i) => ({
  id: supUuid(i + 1),
  code: s.code,
  name: s.name,
  category: s.category,
  status: s.status,
  lead_time_days: s.lead,
  on_time_pct: s.onTime,
  spend_minor: inr(s.spend),
  currency: CURRENCY,
  open_pos: s.openPos,
  contact_name: s.contact,
  contact_email: s.email,
  location: s.location,
  since: s.since,
}));

const supplierByCode = new Map(SUPPLIERS.map((s) => [s.code, s]));
const sup = (code: string): SupplierOut => {
  const found = supplierByCode.get(code);
  if (!found) throw new Error(`Unknown supplier code in fixtures: ${code}`);
  return found;
};

/* ----------------------------- purchase orders ---------------------------- */

type Warehouse = "Bhiwandi DC" | "Pune Plant" | "Chennai Hub" | "Bengaluru Store";

interface PoSeed {
  no: string;
  supplierCode: string;
  status: PurchaseOrderOut["status"];
  warehouse: Warehouse;
  raisedBy: string;
  created: string;
  expected: string;
  received: string | null;
  notes: string | null;
  lines: { sku: string; description: string; qty: number; uom: string; price: number; recv: number }[];
}

const PO_SEEDS: PoSeed[] = [
  {
    no: "PO-2026-0148", supplierCode: "SUP-MER", status: "approved", warehouse: "Pune Plant",
    raisedBy: "Devansh Rao", created: "2026-08-06", expected: "2026-08-22", received: null,
    notes: "Priority run for the Q3 casting line.",
    lines: [
      { sku: "RM-STL-4501", description: "Cold-rolled steel coil, 1.2mm", qty: 40, uom: "MT", price: 62_500, recv: 0 },
      { sku: "RM-STL-4520", description: "Galvanised steel sheet, 0.8mm", qty: 25, uom: "MT", price: 71_200, recv: 0 },
    ],
  },
  {
    no: "PO-2026-0147", supplierCode: "SUP-VLT", status: "approved", warehouse: "Bengaluru Store",
    raisedBy: "Priya Nair", created: "2026-08-09", expected: "2026-08-30", received: null,
    notes: null,
    lines: [
      { sku: "CMP-PCB-220", description: "Control PCB assembly, rev C", qty: 500, uom: "pcs", price: 1_840, recv: 0 },
      { sku: "CMP-REL-014", description: "24V industrial relay", qty: 1200, uom: "pcs", price: 245, recv: 0 },
      { sku: "CMP-CON-088", description: "16-pin ribbon connector", qty: 2000, uom: "pcs", price: 62, recv: 0 },
    ],
  },
  {
    no: "PO-2026-0146", supplierCode: "SUP-APX", status: "received", warehouse: "Bhiwandi DC",
    raisedBy: "Devansh Rao", created: "2026-07-28", expected: "2026-08-05", received: "2026-08-04",
    notes: "Delivered a day early, full quantity.",
    lines: [
      { sku: "PKG-BOX-330", description: "Double-wall export carton, 600×400×400", qty: 5000, uom: "pcs", price: 78, recv: 5000 },
      { sku: "PKG-WRP-011", description: "Stretch wrap film, 500mm", qty: 300, uom: "rolls", price: 640, recv: 300 },
    ],
  },
  {
    no: "PO-2026-0145", supplierCode: "SUP-IRC", status: "approved", warehouse: "Pune Plant",
    raisedBy: "Karthik Iyer", created: "2026-08-01", expected: "2026-08-19", received: null,
    notes: "Follow up — supplier flagged a mill delay.",
    lines: [
      { sku: "RM-ALU-7075", description: "Aluminium billet, 7075-T6", qty: 18, uom: "MT", price: 2_18_000, recv: 0 },
    ],
  },
  {
    no: "PO-2026-0144", supplierCode: "SUP-PLY", status: "received", warehouse: "Chennai Hub",
    raisedBy: "Priya Nair", created: "2026-07-20", expected: "2026-07-30", received: "2026-07-31",
    notes: null,
    lines: [
      { sku: "RM-POL-ABS", description: "ABS polymer granules, natural", qty: 12, uom: "MT", price: 1_34_000, recv: 12 },
      { sku: "RM-POL-PP0", description: "Polypropylene homopolymer", qty: 8, uom: "MT", price: 1_12_000, recv: 8 },
    ],
  },
  {
    no: "PO-2026-0143", supplierCode: "SUP-STL", status: "received", warehouse: "Bengaluru Store",
    raisedBy: "Amit Deshmukh", created: "2026-08-10", expected: "2026-08-16", received: "2026-08-15",
    notes: null,
    lines: [
      { sku: "MRO-LUB-220", description: "Industrial gear oil, ISO 220", qty: 60, uom: "L", price: 385, recv: 60 },
      { sku: "MRO-GLV-XL", description: "Cut-resistant gloves, XL", qty: 400, uom: "pairs", price: 129, recv: 400 },
      { sku: "MRO-FLT-008", description: "Hydraulic filter cartridge", qty: 80, uom: "pcs", price: 640, recv: 80 },
    ],
  },
  {
    no: "PO-2026-0142", supplierCode: "SUP-PRC", status: "closed", warehouse: "Pune Plant",
    raisedBy: "Devansh Rao", created: "2026-06-30", expected: "2026-07-24", received: "2026-07-23",
    notes: "Closed after QC sign-off.",
    lines: [
      { sku: "CMP-CST-410", description: "Gearbox housing casting, GH-410", qty: 300, uom: "pcs", price: 3_150, recv: 300 },
    ],
  },
  {
    no: "PO-2026-0141", supplierCode: "SUP-CRL", status: "approved", warehouse: "Bhiwandi DC",
    raisedBy: "Vikram Sethi", created: "2026-08-12", expected: "2026-08-21", received: null,
    notes: "Inbound freight for the Meridian coil shipment.",
    lines: [
      { sku: "LOG-FRT-FTL", description: "Full-truckload freight, Jamshedpur → Pune", qty: 4, uom: "trips", price: 46_000, recv: 0 },
    ],
  },
  {
    no: "PO-2026-0140", supplierCode: "SUP-VLT", status: "received", warehouse: "Bengaluru Store",
    raisedBy: "Priya Nair", created: "2026-07-15", expected: "2026-08-04", received: "2026-08-06",
    notes: "Two days late — partial short-ship reconciled.",
    lines: [
      { sku: "CMP-SEN-330", description: "Proximity sensor, M18", qty: 800, uom: "pcs", price: 410, recv: 800 },
      { sku: "CMP-TRM-090", description: "DIN-rail terminal block", qty: 3000, uom: "pcs", price: 28, recv: 3000 },
    ],
  },
  {
    no: "PO-2026-0139", supplierCode: "SUP-APX", status: "draft", warehouse: "Chennai Hub",
    raisedBy: "Devansh Rao", created: "2026-08-20", expected: "2026-09-02", received: null,
    notes: "Awaiting budget approval from the plant head.",
    lines: [
      { sku: "PKG-PAL-004", description: "Euro pallet, heat-treated", qty: 600, uom: "pcs", price: 720, recv: 0 },
      { sku: "PKG-STR-020", description: "Steel strapping coil, 19mm", qty: 120, uom: "rolls", price: 1_240, recv: 0 },
    ],
  },
  {
    no: "PO-2026-0138", supplierCode: "SUP-MER", status: "received", warehouse: "Pune Plant",
    raisedBy: "Karthik Iyer", created: "2026-07-02", expected: "2026-07-16", received: "2026-07-16",
    notes: null,
    lines: [
      { sku: "RM-STL-4501", description: "Cold-rolled steel coil, 1.2mm", qty: 55, uom: "MT", price: 61_800, recv: 55 },
    ],
  },
  {
    no: "PO-2026-0137", supplierCode: "SUP-BRF", status: "approved", warehouse: "Bhiwandi DC",
    raisedBy: "Vikram Sethi", created: "2026-08-14", expected: "2026-08-18", received: null,
    notes: "Overdue — driver stuck at the interstate check-post.",
    lines: [
      { sku: "LOG-FRT-LTL", description: "Part-load freight, Kolkata → Bhiwandi", qty: 6, uom: "trips", price: 18_500, recv: 0 },
    ],
  },
  {
    no: "PO-2026-0136", supplierCode: "SUP-IRC", status: "closed", warehouse: "Pune Plant",
    raisedBy: "Devansh Rao", created: "2026-06-10", expected: "2026-07-01", received: "2026-06-29",
    notes: "Closed and invoiced.",
    lines: [
      { sku: "RM-ALU-6061", description: "Aluminium billet, 6061-T6", qty: 22, uom: "MT", price: 1_96_000, recv: 22 },
      { sku: "RM-CU-ROD", description: "Copper rod, 12mm", qty: 6, uom: "MT", price: 7_84_000, recv: 6 },
    ],
  },
  {
    no: "PO-2026-0135", supplierCode: "SUP-ANV", status: "received", warehouse: "Chennai Hub",
    raisedBy: "Amit Deshmukh", created: "2026-07-25", expected: "2026-08-10", received: "2026-08-09",
    notes: "Preventive maintenance on the press line.",
    lines: [
      { sku: "SRV-MNT-PRS", description: "Hydraulic press overhaul, 400T", qty: 1, uom: "job", price: 4_85_000, recv: 1 },
    ],
  },
  {
    no: "PO-2026-0134", supplierCode: "SUP-PLY", status: "draft", warehouse: "Chennai Hub",
    raisedBy: "Priya Nair", created: "2026-08-21", expected: "2026-09-08", received: null,
    notes: null,
    lines: [
      { sku: "RM-POL-PC0", description: "Polycarbonate granules, clear", qty: 10, uom: "MT", price: 2_05_000, recv: 0 },
    ],
  },
];

const poUuid = (n: number): string =>
  `40220000-0000-4000-8000-${n.toString().padStart(12, "0")}`;
const lineUuid = (po: number, n: number): string =>
  `40330000-0000-4000-8000-${po.toString().padStart(6, "0")}${n.toString().padStart(6, "0")}`;

export const PURCHASE_ORDERS: PurchaseOrderOut[] = PO_SEEDS.map((seed, i) => {
  const supplier = sup(seed.supplierCode);
  return {
    id: poUuid(i + 1),
    po_number: seed.no,
    supplier_id: supplier.id,
    supplier_name: supplier.name,
    category: supplier.category,
    status: seed.status,
    currency: CURRENCY,
    warehouse: seed.warehouse,
    raised_by: seed.raisedBy,
    created_date: seed.created,
    expected_date: seed.expected,
    received_date: seed.received,
    notes: seed.notes,
    line_items: seed.lines.map((l, j) => ({
      id: lineUuid(i + 1, j + 1),
      sku: l.sku,
      description: l.description,
      quantity: l.qty,
      uom: l.uom,
      unit_price_minor: inr(l.price),
      received_qty: l.recv,
    })),
  };
});

/* -------------------------------- inventory ------------------------------- */

interface InvSeed {
  sku: string;
  name: string;
  category: Category;
  warehouse: Warehouse;
  onHand: number;
  reorder: number;
  uom: string;
  unitValue: number; // rupees per unit
  updated: string;
}

const INVENTORY_SEEDS: InvSeed[] = [
  { sku: "RM-STL-4501", name: "Cold-rolled steel coil, 1.2mm", category: "raw_materials", warehouse: "Pune Plant", onHand: 82, reorder: 40, uom: "MT", unitValue: 62_400, updated: "2026-08-23" },
  { sku: "RM-STL-4520", name: "Galvanised steel sheet, 0.8mm", category: "raw_materials", warehouse: "Pune Plant", onHand: 21, reorder: 30, uom: "MT", unitValue: 71_000, updated: "2026-08-23" },
  { sku: "RM-ALU-7075", name: "Aluminium billet, 7075-T6", category: "raw_materials", warehouse: "Pune Plant", onHand: 6, reorder: 12, uom: "MT", unitValue: 2_18_000, updated: "2026-08-22" },
  { sku: "RM-ALU-6061", name: "Aluminium billet, 6061-T6", category: "raw_materials", warehouse: "Pune Plant", onHand: 34, reorder: 15, uom: "MT", unitValue: 1_96_000, updated: "2026-08-21" },
  { sku: "RM-CU-ROD", name: "Copper rod, 12mm", category: "raw_materials", warehouse: "Chennai Hub", onHand: 3, reorder: 5, uom: "MT", unitValue: 7_84_000, updated: "2026-08-20" },
  { sku: "RM-POL-ABS", name: "ABS polymer granules, natural", category: "raw_materials", warehouse: "Chennai Hub", onHand: 19, reorder: 10, uom: "MT", unitValue: 1_34_000, updated: "2026-08-22" },
  { sku: "RM-POL-PP0", name: "Polypropylene homopolymer", category: "raw_materials", warehouse: "Chennai Hub", onHand: 7, reorder: 8, uom: "MT", unitValue: 1_12_000, updated: "2026-08-19" },
  { sku: "CMP-PCB-220", name: "Control PCB assembly, rev C", category: "components", warehouse: "Bengaluru Store", onHand: 260, reorder: 300, uom: "pcs", unitValue: 1_840, updated: "2026-08-23" },
  { sku: "CMP-REL-014", name: "24V industrial relay", category: "components", warehouse: "Bengaluru Store", onHand: 4200, reorder: 1500, uom: "pcs", unitValue: 245, updated: "2026-08-23" },
  { sku: "CMP-SEN-330", name: "Proximity sensor, M18", category: "components", warehouse: "Bengaluru Store", onHand: 640, reorder: 400, uom: "pcs", unitValue: 410, updated: "2026-08-22" },
  { sku: "CMP-CST-410", name: "Gearbox housing casting, GH-410", category: "components", warehouse: "Pune Plant", onHand: 40, reorder: 120, uom: "pcs", unitValue: 3_150, updated: "2026-08-21" },
  { sku: "CMP-CON-088", name: "16-pin ribbon connector", category: "components", warehouse: "Bengaluru Store", onHand: 8800, reorder: 3000, uom: "pcs", unitValue: 62, updated: "2026-08-20" },
  { sku: "PKG-BOX-330", name: "Double-wall export carton", category: "packaging", warehouse: "Bhiwandi DC", onHand: 12400, reorder: 5000, uom: "pcs", unitValue: 78, updated: "2026-08-23" },
  { sku: "PKG-WRP-011", name: "Stretch wrap film, 500mm", category: "packaging", warehouse: "Bhiwandi DC", onHand: 210, reorder: 250, uom: "rolls", unitValue: 640, updated: "2026-08-22" },
  { sku: "PKG-PAL-004", name: "Euro pallet, heat-treated", category: "packaging", warehouse: "Bhiwandi DC", onHand: 1350, reorder: 500, uom: "pcs", unitValue: 720, updated: "2026-08-19" },
  { sku: "MRO-LUB-220", name: "Industrial gear oil, ISO 220", category: "mro", warehouse: "Bengaluru Store", onHand: 180, reorder: 60, uom: "L", unitValue: 385, updated: "2026-08-21" },
  { sku: "MRO-GLV-XL", name: "Cut-resistant gloves, XL", category: "mro", warehouse: "Bengaluru Store", onHand: 90, reorder: 200, uom: "pairs", unitValue: 129, updated: "2026-08-20" },
  { sku: "MRO-FLT-008", name: "Hydraulic filter cartridge", category: "mro", warehouse: "Pune Plant", onHand: 240, reorder: 80, uom: "pcs", unitValue: 640, updated: "2026-08-18" },
];

const invUuid = (n: number): string =>
  `60440000-0000-4000-8000-${n.toString().padStart(12, "0")}`;

export const INVENTORY: InventoryItemOut[] = INVENTORY_SEEDS.map((s, i) => ({
  id: invUuid(i + 1),
  sku: s.sku,
  name: s.name,
  category: s.category,
  warehouse: s.warehouse,
  on_hand: s.onHand,
  on_hand_qty: String(s.onHand),
  reserved_qty: "0",
  available_qty: String(s.onHand),
  reorder_point: s.reorder,
  uom: s.uom,
  unit_value_minor: inr(s.unitValue),
  currency: CURRENCY,
  updated_at: s.updated,
  item_category_id: null,
  secondary_uom_id: null,
  hsn_code: "",
  track_lot: false,
  track_roll: false,
  track_expiry: false,
  track_quality: false,
  is_active: true,
}));

/* ------------------------------ item master (Phase 1) ---------------------- */

const categoryUuid = (n: number): string =>
  `90770000-0000-4000-8000-${n.toString().padStart(12, "0")}`;

/** A small illustrative tree — enough for the category picker and list filter to render
 *  something real in fixture mode. Mutated in place by fixture writes, like `SUPPLIERS`. */
export const ITEM_CATEGORIES: ItemCategoryOut[] = [
  { id: categoryUuid(1), code: "METALS", name: "Metals", parent_id: null },
  { id: categoryUuid(2), code: "POLYMERS", name: "Polymers", parent_id: null },
  { id: categoryUuid(3), code: "FASTENERS", name: "Fasteners", parent_id: null },
  { id: categoryUuid(4), code: "STEEL", name: "Steel", parent_id: categoryUuid(1) },
  { id: categoryUuid(5), code: "ALUMINIUM", name: "Aluminium", parent_id: categoryUuid(1) },
];

const uomUuid = (n: number): string => `91880000-0000-4000-8000-${n.toString().padStart(12, "0")}`;

/** The fixed-registry units a fresh organization sees, mirroring
 *  `app.inventory.quantity.UNIT_NAMES` closely enough for fixture-mode pickers. */
export const UOMS: UomOut[] = [
  { code: "mm", name: "Millimetre", dimension: "length" },
  { code: "cm", name: "Centimetre", dimension: "length" },
  { code: "m", name: "Metre", dimension: "length" },
  { code: "mg", name: "Milligram", dimension: "mass" },
  { code: "g", name: "Gram", dimension: "mass" },
  { code: "kg", name: "Kilogram", dimension: "mass" },
  { code: "t", name: "Tonne", dimension: "mass" },
  { code: "l", name: "Litre", dimension: "volume" },
  { code: "pcs", name: "Piece", dimension: "count" },
  { code: "box", name: "Box", dimension: "box" },
  { code: "roll", name: "Roll", dimension: "roll" },
].map((u, i) => ({
  id: uomUuid(i + 1),
  code: u.code,
  name: u.name,
  dimension: u.dimension,
  is_discrete: ["pcs", "box", "roll"].includes(u.code),
  is_system: true,
}));

/** Mutated in place by fixture writes, keyed by inventory item id — same shape `MOVEMENTS`
 *  already uses for a one-to-many fixture relationship. */
export const ITEM_ATTRIBUTES: Record<string, ItemAttributeOut[]> = {};

export const ITEM_VARIANTS: Record<string, ItemVariantOut[]> = {};

/* -------------------------------- warehouses ------------------------------- */

const WAREHOUSE_SEEDS: { code: string; name: Warehouse; location: string }[] = [
  { code: "WH-BHW", name: "Bhiwandi DC", location: "Bhiwandi, Maharashtra" },
  { code: "WH-PUN", name: "Pune Plant", location: "Chakan, Pune" },
  { code: "WH-CHN", name: "Chennai Hub", location: "Sriperumbudur, Chennai" },
  { code: "WH-BLR", name: "Bengaluru Store", location: "Peenya, Bengaluru" },
];

const whUuid = (n: number): string => `70550000-0000-4000-8000-${n.toString().padStart(12, "0")}`;

export const WAREHOUSES: WarehouseOut[] = WAREHOUSE_SEEDS.map((w, i) => ({
  id: whUuid(i + 1),
  code: w.code,
  name: w.name,
  location: w.location,
}));

/* -------------------------------- locations -------------------------------- */

const locUuid = (n: number): string => `70560000-0000-4000-8000-${n.toString().padStart(12, "0")}`;

/**
 * The Bhiwandi DC tree (`WAREHOUSES[0]`) — a zone nested down to bin, a couple of functional
 * top-level areas, and the default bin every warehouse is created with. Every other warehouse
 * carries none, on purpose: fixture mode renders the tree's empty state exactly as a brand-new
 * warehouse's does live.
 */
const BHIWANDI_ID = whUuid(1);

interface LocationSeed {
  n: number;
  parentN: number | null;
  kind: LocationOut["kind"];
  code: string;
  name: string;
  isDefault?: boolean;
  barcode?: string;
  pickSequence?: number;
  maxWeightGrams?: number;
  maxVolumeMilli?: number;
  storageType?: string;
}

const LOCATION_SEEDS: LocationSeed[] = [
  { n: 1, parentN: null, kind: "zone", code: "ZONE-A", name: "Zone A — Raw materials", pickSequence: 1 },
  { n: 2, parentN: 1, kind: "aisle", code: "A1", name: "Aisle A1", pickSequence: 1 },
  { n: 3, parentN: 2, kind: "rack", code: "A1-R1", name: "Rack A1-R1", pickSequence: 1 },
  { n: 4, parentN: 3, kind: "shelf", code: "A1-R1-S1", name: "Shelf A1-R1-S1", pickSequence: 1 },
  {
    n: 5,
    parentN: 4,
    kind: "bin",
    code: "A1-R1-S1-B1",
    name: "Bin A1-R1-S1-B1",
    barcode: "LOC-A1R1S1B1",
    pickSequence: 1,
    maxWeightGrams: 25000,
    maxVolumeMilli: 40000,
    storageType: "pallet",
  },
  {
    n: 6,
    parentN: 4,
    kind: "bin",
    code: "A1-R1-S1-B2",
    name: "Bin A1-R1-S1-B2",
    barcode: "LOC-A1R1S1B2",
    pickSequence: 2,
    maxWeightGrams: 25000,
    maxVolumeMilli: 40000,
    storageType: "pallet",
  },
  { n: 7, parentN: null, kind: "receiving", code: "RECV", name: "Receiving dock" },
  { n: 8, parentN: null, kind: "dock", code: "DOCK-1", name: "Dispatch dock 1" },
  { n: 9, parentN: null, kind: "bin", code: "DEFAULT", name: "Default bin", isDefault: true },
];

export const LOCATIONS: Record<string, LocationOut[]> = {
  [BHIWANDI_ID]: LOCATION_SEEDS.map((s) => ({
    id: locUuid(s.n),
    warehouse_id: BHIWANDI_ID,
    parent_id: s.parentN === null ? null : locUuid(s.parentN),
    kind: s.kind,
    code: s.code,
    name: s.name,
    is_default: s.isDefault ?? false,
    barcode: s.barcode ?? null,
    pick_sequence: s.pickSequence ?? null,
    max_weight_grams: s.maxWeightGrams ?? null,
    max_volume_milli: s.maxVolumeMilli ?? null,
    storage_type: s.storageType ?? null,
  })),
};

/* ---------------------------- stock movements ------------------------------ */

const movementUuid = (item: number, n: number): string =>
  `80660000-0000-4000-8000-${item.toString().padStart(6, "0")}${n.toString().padStart(6, "0")}`;

/** One opening movement per seeded item, so the ledger invariant (on_hand == sum of deltas)
 *  holds in fixture mode too. Keyed by inventory item id. */
export const MOVEMENTS: Record<string, StockMovementOut[]> = Object.fromEntries(
  INVENTORY.map((item, i) => [
    item.id,
    [
      {
        id: movementUuid(i + 1, 1),
        kind: "opening" as const,
        reason: null,
        quantity_delta: item.on_hand,
        on_hand_after: item.on_hand,
        quantity: item.on_hand_qty,
        balance_after: item.on_hand_qty,
        location_id: "fixture-default-bin",
        status: "AVAILABLE" as const,
        reference_type: null,
        reference_id: null,
        note: "Opening balance",
        occurred_on: item.updated_at,
        created_at: `${item.updated_at}T09:00:00Z`,
        created_by: "seed",
      },
    ],
  ]),
);
