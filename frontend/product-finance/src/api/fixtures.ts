/**
 * Realistic fixture data for the Finance product, shaped exactly like the responses a
 * `finance-service` will return, so the whole UI renders without a live login. Every amount is an
 * integer number of minor units (paise); totals are *computed* from the lines here rather than
 * hand-typed, so an invoice always foots exactly. Swapped out by `USE_FIXTURES` in `client.ts`.
 */

import type {
  CashflowPoint,
  Invoice,
  InvoiceLine,
  InvoiceStatus,
  LedgerAccount,
  Payment,
  PaymentAllocation,
} from "./types";

/** Today, in the fixture world. Used to age invoices into sent / overdue. */
export const TODAY = "2026-08-24";

/** Rupees → paise. All seed figures below are written in rupees for legibility. */
const paise = (rupees: number): number => Math.round(rupees * 100);

/* ----------------------------- invoice seeds ----------------------------- */

interface LineSeed {
  description: string;
  quantity: number;
  /** Unit price in rupees. */
  rate: number;
}

interface InvoiceSeed {
  number: string;
  document_type?: "invoice" | "credit_note";
  customer_id: string;
  issued_date: string;
  due_date: string;
  tax_rate_pct: number;
  /** Force a lifecycle state the amounts can't imply (draft, void). */
  status_override?: InvoiceStatus;
  corrects?: string;
  notes?: string;
  lines: LineSeed[];
}

const INVOICE_SEEDS: InvoiceSeed[] = [
  {
    number: "INV-2026-0120", customer_id: "cust-06", issued_date: "2026-03-10", due_date: "2026-04-09", tax_rate_pct: 18,
    notes: "Site delivery, Raipur plant. Payment overdue — third reminder issued.",
    lines: [{ description: "MS plates 10mm (tonnes)", quantity: 18, rate: 58000 }],
  },
  {
    number: "INV-2026-0122", customer_id: "cust-08", issued_date: "2026-05-01", due_date: "2026-05-31", tax_rate_pct: 18,
    lines: [{ description: "Teak wood logs, seasoned (cft)", quantity: 300, rate: 3200 }],
  },
  {
    number: "INV-2026-0130", customer_id: "cust-01", issued_date: "2026-04-15", due_date: "2026-05-15", tax_rate_pct: 18,
    lines: [{ description: "Cotton yarn — 30s count (kg)", quantity: 1800, rate: 265 }],
  },
  {
    number: "INV-2026-0137", customer_id: "cust-03", issued_date: "2026-04-28", due_date: "2026-05-28", tax_rate_pct: 18,
    lines: [{ description: "Line-haul freight — Pune⇄Mumbai (trips)", quantity: 30, rate: 18000 }],
  },
  {
    number: "INV-2026-0142", customer_id: "cust-01", issued_date: "2026-05-02", due_date: "2026-06-01", tax_rate_pct: 18,
    lines: [
      { description: "Cotton yarn — 40s count (kg)", quantity: 2400, rate: 285 },
      { description: "Dyeing & finishing charges", quantity: 1, rate: 65000 },
    ],
  },
  {
    number: "INV-2026-0145", customer_id: "cust-04", issued_date: "2026-05-20", due_date: "2026-06-19", tax_rate_pct: 18,
    lines: [{ description: "Ceiling fan — BLDC (units)", quantity: 700, rate: 2350 }],
  },
  {
    number: "INV-2026-0148", customer_id: "cust-02", issued_date: "2026-06-10", due_date: "2026-07-10", tax_rate_pct: 5,
    lines: [
      { description: "Packaged spices — bulk (cartons)", quantity: 320, rate: 1250 },
      { description: "Cold storage handling", quantity: 1, rate: 28000 },
    ],
  },
  {
    number: "INV-2026-0151", customer_id: "cust-03", issued_date: "2026-06-18", due_date: "2026-07-18", tax_rate_pct: 18,
    lines: [
      { description: "Line-haul freight — Pune⇄Delhi (trips)", quantity: 42, rate: 18500 },
      { description: "Fuel surcharge", quantity: 1, rate: 54000 },
    ],
  },
  {
    number: "INV-2026-0155", customer_id: "cust-04", issued_date: "2026-07-01", due_date: "2026-07-31", tax_rate_pct: 18,
    notes: "Partly paid. Credit note CN-2026-0008 issued for 30 returned panels.",
    lines: [
      { description: "LED panel 40W (units)", quantity: 1500, rate: 640 },
      { description: "Installation labour (site-days)", quantity: 12, rate: 4200 },
    ],
  },
  {
    number: "INV-2026-0158", customer_id: "cust-02", issued_date: "2026-07-14", due_date: "2026-08-13", tax_rate_pct: 5,
    lines: [{ description: "Edible oil — refined (litres)", quantity: 6000, rate: 145 }],
  },
  {
    number: "INV-2026-0160", customer_id: "cust-05", issued_date: "2026-07-20", due_date: "2026-08-19", tax_rate_pct: 18,
    lines: [
      { description: "Managed hosting — August (nodes)", quantity: 24, rate: 8500 },
      { description: "Support retainer", quantity: 1, rate: 75000 },
    ],
  },
  {
    number: "INV-2026-0163", customer_id: "cust-06", issued_date: "2026-07-28", due_date: "2026-08-27", tax_rate_pct: 18,
    lines: [
      { description: "TMT bars Fe500 (tonnes)", quantity: 60, rate: 62000 },
      { description: "Cutting & bending", quantity: 1, rate: 145000 },
    ],
  },
  {
    number: "INV-2026-0166", customer_id: "cust-07", issued_date: "2026-08-02", due_date: "2026-09-01", tax_rate_pct: 12,
    notes: "Advance of ₹3,00,000 received against this invoice.",
    lines: [
      { description: "API — Paracetamol grade (kg)", quantity: 800, rate: 1450 },
      { description: "QC batch testing", quantity: 6, rate: 18000 },
    ],
  },
  {
    number: "INV-2026-0169", customer_id: "cust-08", issued_date: "2026-08-05", due_date: "2026-09-04", tax_rate_pct: 18,
    notes: "Credit note CN-2026-0012 issued for 40 damaged sheets.",
    lines: [
      { description: "Marine plywood 18mm (sheets)", quantity: 900, rate: 2100 },
      { description: "Freight to yard", quantity: 1, rate: 42000 },
    ],
  },
  {
    number: "INV-2026-0170", customer_id: "cust-09", issued_date: "2026-08-06", due_date: "2026-09-05", tax_rate_pct: 18,
    status_override: "void", notes: "Voided — superseded by INV-2026-0172 after a spec change.",
    lines: [{ description: "Reception desk — custom (units)", quantity: 2, rate: 145000 }],
  },
  {
    number: "INV-2026-0172", customer_id: "cust-09", issued_date: "2026-08-08", due_date: "2026-09-07", tax_rate_pct: 18,
    lines: [
      { description: "Modular workstation (units)", quantity: 48, rate: 22500 },
      { description: "On-site assembly", quantity: 1, rate: 96000 },
    ],
  },
  {
    number: "INV-2026-0175", customer_id: "cust-10", issued_date: "2026-08-12", due_date: "2026-09-11", tax_rate_pct: 18,
    status_override: "draft", notes: "Draft — awaiting final headcount from the events team.",
    lines: [
      { description: "Banquet catering (per plate)", quantity: 850, rate: 1250 },
      { description: "Décor & floral", quantity: 1, rate: 185000 },
    ],
  },
  {
    number: "INV-2026-0178", customer_id: "cust-11", issued_date: "2026-08-15", due_date: "2026-09-14", tax_rate_pct: 5,
    notes: "Part payment of ₹2,00,000 received on account.",
    lines: [
      { description: "Drip irrigation kit (acres)", quantity: 120, rate: 7800 },
      { description: "Agronomy advisory", quantity: 1, rate: 60000 },
    ],
  },
  {
    number: "INV-2026-0181", customer_id: "cust-12", issued_date: "2026-08-18", due_date: "2026-09-17", tax_rate_pct: 28,
    lines: [
      { description: "Spare parts — brake assembly (sets)", quantity: 220, rate: 3400 },
      { description: "Logistics & handling", quantity: 1, rate: 36000 },
    ],
  },
  // Credit notes — append-only corrections, never edits to the original invoice.
  {
    number: "CN-2026-0008", document_type: "credit_note", customer_id: "cust-04", issued_date: "2026-07-22", due_date: "2026-07-22",
    tax_rate_pct: 18, corrects: "INV-2026-0155", notes: "Correction: 30 LED panels returned as faulty.",
    lines: [{ description: "Return — LED panel 40W (units)", quantity: -30, rate: 640 }],
  },
  {
    number: "CN-2026-0012", document_type: "credit_note", customer_id: "cust-08", issued_date: "2026-08-14", due_date: "2026-08-14",
    tax_rate_pct: 18, corrects: "INV-2026-0169", notes: "Correction: 40 marine plywood sheets damaged in transit.",
    lines: [{ description: "Return — Marine plywood 18mm (sheets)", quantity: -40, rate: 2100 }],
  },
];

/* ------------------------------ payment seeds ----------------------------- */

interface AllocSeed {
  number: string;
  /** Amount applied, in rupees. */
  amount: number;
}

interface PaymentSeed {
  reference: string;
  customer_id: string;
  received_date: string;
  method: Payment["method"];
  note?: string;
  allocations: AllocSeed[];
}

const PAYMENT_SEEDS: PaymentSeed[] = [
  { reference: "PMT-2026-0288", customer_id: "cust-01", received_date: "2026-05-14", method: "bank_transfer", note: "NEFT · UTR AXIS0288", allocations: [{ number: "INV-2026-0130", amount: 562860 }] },
  { reference: "PMT-2026-0295", customer_id: "cust-03", received_date: "2026-05-27", method: "cheque", note: "Cheque 114520 · HDFC", allocations: [{ number: "INV-2026-0137", amount: 637200 }] },
  { reference: "PMT-2026-0301", customer_id: "cust-01", received_date: "2026-06-01", method: "bank_transfer", note: "RTGS · UTR ICIC0301", allocations: [{ number: "INV-2026-0142", amount: 883820 }] },
  { reference: "PMT-2026-0305", customer_id: "cust-04", received_date: "2026-06-18", method: "bank_transfer", note: "NEFT · UTR SBIN0305", allocations: [{ number: "INV-2026-0145", amount: 1941100 }] },
  { reference: "PMT-2026-0331", customer_id: "cust-02", received_date: "2026-07-09", method: "upi", note: "UPI · kanaka@okhdfcbank", allocations: [{ number: "INV-2026-0148", amount: 449400 }] },
  { reference: "PMT-2026-0352", customer_id: "cust-04", received_date: "2026-08-10", method: "bank_transfer", note: "NEFT · part settlement", allocations: [{ number: "INV-2026-0155", amount: 500000 }] },
  { reference: "PMT-2026-0357", customer_id: "cust-08", received_date: "2026-08-20", method: "upi", note: "UPI · coastal@okicici", allocations: [{ number: "INV-2026-0169", amount: 800000 }] },
  { reference: "PMT-2026-0360", customer_id: "cust-07", received_date: "2026-08-22", method: "card", note: "Corporate card · advance", allocations: [{ number: "INV-2026-0166", amount: 300000 }] },
  { reference: "PMT-2026-0364", customer_id: "cust-11", received_date: "2026-08-23", method: "cash", note: "Cash · receipt 0364", allocations: [{ number: "INV-2026-0178", amount: 200000 }] },
];

/* -------------------------------- assembly -------------------------------- */

function buildLines(number: string, seeds: LineSeed[]): InvoiceLine[] {
  return seeds.map((line, i) => {
    const unit = paise(line.rate);
    return {
      id: `${number}-L${i + 1}`,
      description: line.description,
      quantity: line.quantity,
      unit_price_minor: unit,
      amount_minor: unit * line.quantity,
    };
  });
}

/** Paid-so-far per invoice number, summed from every payment allocation. */
const PAID_BY_NUMBER = new Map<string, number>();
for (const p of PAYMENT_SEEDS) {
  for (const a of p.allocations) {
    PAID_BY_NUMBER.set(a.number, (PAID_BY_NUMBER.get(a.number) ?? 0) + paise(a.amount));
  }
}

function deriveStatus(seed: InvoiceSeed, total: number, paid: number): InvoiceStatus {
  if (seed.status_override) return seed.status_override;
  // A credit note is an issued correction — it has no paid/overdue lifecycle of its own.
  if (seed.document_type === "credit_note") return "sent";
  if (paid >= total) return "paid";
  return seed.due_date < TODAY ? "overdue" : "sent";
}

export const INVOICES: Invoice[] = INVOICE_SEEDS.map((seed) => {
  const lines = buildLines(seed.number, seed.lines);
  const subtotal = lines.reduce((sum, l) => sum + l.amount_minor, 0);
  const tax = Math.round((subtotal * seed.tax_rate_pct) / 100);
  const total = subtotal + tax;
  const paid = PAID_BY_NUMBER.get(seed.number) ?? 0;
  return {
    id: `inv-${seed.number}`,
    number: seed.number,
    document_type: seed.document_type ?? "invoice",
    customer_id: seed.customer_id,
    issued_date: seed.issued_date,
    due_date: seed.due_date,
    status: deriveStatus(seed, total, paid),
    currency: "INR",
    lines,
    subtotal_minor: subtotal,
    tax_rate_pct: seed.tax_rate_pct,
    tax_minor: tax,
    total_minor: total,
    amount_paid_minor: paid,
    notes: seed.notes ?? null,
    corrects_invoice_id: seed.corrects ? `inv-${seed.corrects}` : null,
  };
});

const INVOICE_BY_NUMBER = new Map(INVOICES.map((inv) => [inv.number, inv]));

export const PAYMENTS: Payment[] = PAYMENT_SEEDS.map((seed) => {
  const allocations: PaymentAllocation[] = seed.allocations.map((a) => ({
    invoice_id: `inv-${a.number}`,
    invoice_number: a.number,
    amount_minor: paise(a.amount),
  }));
  const amount = allocations.reduce((sum, a) => sum + a.amount_minor, 0);
  return {
    id: `pmt-${seed.reference}`,
    reference: seed.reference,
    customer_id: seed.customer_id,
    received_date: seed.received_date,
    method: seed.method,
    amount_minor: amount,
    allocations,
    note: seed.note ?? null,
  };
}).sort((a, b) => (a.received_date < b.received_date ? 1 : -1));

export function invoiceByNumber(number: string): Invoice | null {
  return INVOICE_BY_NUMBER.get(number) ?? null;
}

/* ------------------------ cash movement & the books ----------------------- */

/** Six months of cash in / out for the overview area chart (representative aggregate). */
export const CASHFLOW: CashflowPoint[] = [
  { label: "Mar", inflow_minor: paise(3_820_000), outflow_minor: paise(2_610_000) },
  { label: "Apr", inflow_minor: paise(5_240_000), outflow_minor: paise(3_180_000) },
  { label: "May", inflow_minor: paise(6_180_000), outflow_minor: paise(3_460_000) },
  { label: "Jun", inflow_minor: paise(4_920_000), outflow_minor: paise(3_890_000) },
  { label: "Jul", inflow_minor: paise(7_150_000), outflow_minor: paise(4_120_000) },
  { label: "Aug", inflow_minor: paise(5_860_000), outflow_minor: paise(4_530_000) },
];

/** Bank + cash on hand across accounts — no invoice can imply this, so it is a booked figure. */
export const CASH_BALANCE_MINOR = paise(18_640_000);

/** Trend series + deltas for the four overview KPIs (scaled to lakh for the sparkline shape). */
export const KPI_TRENDS = {
  revenue: { series: [42, 51, 55, 49, 71, 63, 70], deltaPct: 8.4 },
  receivables: { series: [58, 61, 64, 69, 66, 72, 74], deltaPct: 6.1 },
  overdue: { series: [12, 9, 14, 11, 16, 19, 21], deltaPct: 12.7 },
  cash: { series: [141, 152, 149, 168, 172, 179, 186], deltaPct: 3.9 },
} as const;

/** P&L / trial-balance accounts, FY-to-date. Debits and credits are booked figures. */
export const LEDGER_ACCOUNTS: LedgerAccount[] = [
  { code: "4000", name: "Sales — Goods", type: "revenue", debit_minor: 0, credit_minor: paise(48_200_000) },
  { code: "4100", name: "Sales — Services", type: "revenue", debit_minor: 0, credit_minor: paise(9_640_000) },
  { code: "4200", name: "Freight recovered", type: "revenue", debit_minor: 0, credit_minor: paise(1_820_000) },
  { code: "4900", name: "Sales returns & credit notes", type: "contra_revenue", debit_minor: paise(1_240_000), credit_minor: 0 },
  { code: "5000", name: "Cost of goods sold", type: "expense", debit_minor: paise(31_500_000), credit_minor: 0 },
  { code: "5100", name: "Salaries & wages", type: "expense", debit_minor: paise(8_900_000), credit_minor: 0 },
  { code: "5200", name: "Rent & utilities", type: "expense", debit_minor: paise(2_150_000), credit_minor: 0 },
  { code: "5300", name: "Logistics & freight", type: "expense", debit_minor: paise(3_420_000), credit_minor: 0 },
  { code: "5400", name: "Marketing & sales", type: "expense", debit_minor: paise(1_760_000), credit_minor: 0 },
  { code: "5500", name: "Depreciation", type: "expense", debit_minor: paise(980_000), credit_minor: 0 },
];
