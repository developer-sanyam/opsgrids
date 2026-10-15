/**
 * Realistic fixture data for the Finance product, shaped exactly like the responses a
 * `finance-service` will return, so the whole UI renders without a live login. Every amount is an
 * integer number of minor units (paise); totals are *computed* from the lines here rather than
 * hand-typed, so an invoice always foots exactly. Swapped out by `USE_FIXTURES` in `client.ts`.
 */

import { FIXTURE_CUSTOMERS } from "@/data/customers";
import type {
  EInvoiceFiling,
  EInvoiceState,
  EwayBillWithInvoice,
  FilingStatus,
  Gstr3bSections,
  GstRegistration,
  GstReturn,
  GstReturnDetail,
  GstReturnTransition,
  ImsAction,
  ImsDecision,
  ImsDocument,
  ImsDocumentDetail,
  ImsExpenseCategory,
  ImsReason,
  Invoice,
  InvoiceLine,
  InvoiceStatus,
  LedgerAccount,
  Payment,
  PaymentAllocation,
  PurchaseDocument,
  ReturnDiff,
  ReturnTotals,
  SupplierField,
  SupplierSettings,
  SupplyType,
  TaxHeadAmounts,
  WithholdingRate,
  WithholdingWithInvoice,
} from "./types";

/** Today, in the fixture world. Used to age invoices into sent / overdue. */
export const TODAY = "2026-08-24";

/**
 * The instant the fixture world is frozen at. Only the e-invoice cancellation window reads it:
 * `remedy` is a deadline compared to a clock, and comparing to the *browser's* clock would make a
 * fixture screen say something the live one never does — live, the service answers that question
 * from stored facts and the UI only renders the answer.
 */
const NOW = "2026-08-24T11:20:00Z";

/** This organization's own GSTIN. Maharashtra, matching the demo seed's registration. */
export const SELLER_GSTIN = "27AABCA9012D1Z5";
const SELLER_STATE_CODE = "27";

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
    number: "INV-2026-0168", customer_id: "cust-13", issued_date: "2026-08-04", due_date: "2026-09-03", tax_rate_pct: 5,
    notes: "Unregistered buyer — a B2C supply, which carries no e-invoice obligation.",
    lines: [{ description: "Bulk catering — staff canteen (per plate)", quantity: 1200, rate: 180 }],
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

/* ------------------------------ GST & e-invoicing --------------------------- *
 * The compliance half of every document, kept in one block rather than threaded through the
 * seeds above. HSN codes sit in a per-invoice list because they are per *line* and positional,
 * and interleaving them into the commercial seeds would bury the thing those seeds are for.  */

/** HSN (goods) / SAC (services) per line, in line order. */
const HSN_BY_INVOICE: Record<string, string[]> = {
  "INV-2026-0120": ["72085190"],
  "INV-2026-0122": ["44039922"],
  "INV-2026-0130": ["52051110"],
  "INV-2026-0137": ["996511"],
  "INV-2026-0142": ["52051210", "998821"],
  "INV-2026-0145": ["84145110"],
  "INV-2026-0148": ["09109929", "996729"],
  "INV-2026-0151": ["996511", "996511"],
  "INV-2026-0155": ["94054090", "995461"],
  "INV-2026-0158": ["15079010"],
  "INV-2026-0160": ["998315", "998313"],
  "INV-2026-0163": ["72142090", "998873"],
  "INV-2026-0166": ["29242930", "998346"],
  "INV-2026-0168": ["996334"],
  "INV-2026-0169": ["44121000", "996511"],
  "INV-2026-0170": ["94033000"],
  "INV-2026-0172": ["94033000", "995466"],
  "INV-2026-0175": ["996334", "003"],
  "INV-2026-0178": ["84248210", "998619"],
  "INV-2026-0181": ["87083000", "996511"],
  "CN-2026-0008": ["94054090"],
  "CN-2026-0012": ["44121000"],
};

/**
 * One recorded attempt at registering a document, newest last. A retry is a further entry: the
 * filing table is append-only, so this is a history and never a status to overwrite.
 */
interface FilingSeed {
  status: FilingStatus;
  /** When the attempt was made. */
  at: string;
  /** The portal's numbered code, or one of the platform's named ones. */
  error_code?: string;
  error?: string;
}

/**
 * Filing history per document. Everything the compliance screens have to distinguish appears at
 * least once: a clean registration, a retried one, a portal refusal, a run of outages that spends
 * the retry budget, a withdrawal, an attempt still in flight, and documents nobody has filed yet.
 */
const FILING_SEEDS: Record<string, FilingSeed[]> = {
  "INV-2026-0120": [{ status: "filed", at: "2026-03-10T09:12:44Z" }],
  "INV-2026-0122": [{ status: "filed", at: "2026-05-01T10:04:18Z" }],
  "INV-2026-0130": [{ status: "filed", at: "2026-04-15T08:55:02Z" }],
  "INV-2026-0137": [{ status: "filed", at: "2026-04-28T12:31:57Z" }],
  "INV-2026-0142": [{ status: "filed", at: "2026-05-02T09:47:11Z" }],
  "INV-2026-0145": [{ status: "filed", at: "2026-05-20T11:02:39Z" }],
  "INV-2026-0148": [{ status: "filed", at: "2026-06-10T07:41:20Z" }],
  // An outage, then a clean registration on the retry — the ordinary happy path with a wobble.
  "INV-2026-0151": [
    { status: "failed", at: "2026-06-18T06:15:03Z", error_code: "unavailable", error: "The Invoice Registration Portal did not respond" },
    { status: "filed", at: "2026-06-18T06:17:11Z" },
  ],
  "INV-2026-0155": [{ status: "filed", at: "2026-07-01T09:20:44Z" }],
  "INV-2026-0158": [{ status: "filed", at: "2026-07-14T10:11:08Z" }],
  "INV-2026-0160": [{ status: "filed", at: "2026-07-20T08:33:26Z" }],
  // The portal refused it outright. Terminal on the first attempt: retrying identical bytes is
  // how an integration goes quiet and a deadline passes.
  "INV-2026-0163": [
    {
      status: "failed",
      at: "2026-07-28T09:05:12Z",
      error_code: "2189",
      error: "HSN code 998873 is not valid for the supply type declared",
    },
  ],
  "INV-2026-0166": [{ status: "filed", at: "2026-08-02T11:26:05Z" }],
  "INV-2026-0169": [{ status: "filed", at: "2026-08-05T10:48:52Z" }],
  // Registered, then withdrawn inside the window after the spec changed.
  "INV-2026-0170": [
    { status: "filed", at: "2026-08-06T09:14:30Z" },
    { status: "cancelled", at: "2026-08-06T16:52:07Z" },
  ],
  // Five outages, budget spent. The platform has stopped and is saying so.
  "INV-2026-0172": [
    { status: "failed", at: "2026-08-08T07:02:00Z", error_code: "unavailable", error: "The Invoice Registration Portal did not respond" },
    { status: "failed", at: "2026-08-08T07:04:00Z", error_code: "unavailable", error: "The Invoice Registration Portal did not respond" },
    { status: "failed", at: "2026-08-08T07:08:00Z", error_code: "unavailable", error: "The Invoice Registration Portal did not respond" },
    { status: "failed", at: "2026-08-08T07:16:00Z", error_code: "unavailable", error: "The Invoice Registration Portal did not respond" },
    { status: "failed", at: "2026-08-08T07:32:00Z", error_code: "retry_exhausted", error: "Five attempts made; the platform has stopped retrying" },
  ],
  // Filed late yesterday, so the portal's 24-hour cancellation window is still open.
  "INV-2026-0181": [{ status: "filed", at: "2026-08-24T06:40:00Z" }],
  // In flight right now: attempted once, awaiting the retry.
  "INV-2026-0178": [
    { status: "pending", at: "2026-08-24T10:58:00Z" },
  ],
  "CN-2026-0008": [{ status: "filed", at: "2026-07-22T13:05:41Z" }],
  "CN-2026-0012": [{ status: "filed", at: "2026-08-14T09:58:19Z" }],
  // INV-2026-0175 is a draft and INV-2026-0168 is B2C — neither has an attempt, for opposite
  // reasons, which is exactly the pair a filing column has to tell apart.
};

/** A deterministic hex string of `length` characters, seeded by `key`. FNV-1a, expanded. */
function hexFrom(key: string, length: number): string {
  let hash = 0x811c9dc5;
  let out = "";
  let round = 0;
  while (out.length < length) {
    for (const ch of `${key}#${round}`) {
      hash ^= ch.codePointAt(0) ?? 0;
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    out += hash.toString(16).padStart(8, "0");
    round += 1;
  }
  return out.slice(0, length);
}

/** Base64url without padding — the alphabet every segment of a signed QR is written in. */
function b64url(text: string): string {
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * A signed QR the exact shape the portal returns: three base64url segments, the middle one
 * carrying the fields the printed code shows in the portal's own key names.
 *
 * The signature is derived, not computed — nothing in a browser holds NIC's key, and the header
 * says `stub` so a fixture code can never be mistaken for a registered one by whatever reads it.
 */
function qrPayload(invoice: { number: string; total_minor: number; lines: InvoiceLine[] }, irn: string, buyerGstin: string | null, at: string): string {
  const header = b64url(JSON.stringify({ alg: "HS256", typ: "JWT", stub: true, portal: "nic-einvoice-stub" }));
  const body = b64url(
    JSON.stringify({
      SellerGstin: SELLER_GSTIN,
      BuyerGstin: buyerGstin ?? "URP",
      DocNo: invoice.number,
      DocTyp: invoice.number.startsWith("CN-") ? "credit_note" : "invoice",
      DocDt: at.slice(8, 10) + "/" + at.slice(5, 7) + "/" + at.slice(0, 4),
      TotInvVal: (invoice.total_minor / 100).toFixed(2),
      ItemCnt: invoice.lines.length,
      MainHsnCode: invoice.lines[0]?.hsn_sac ?? null,
      Irn: irn,
      IrnDt: `${at.slice(0, 10)} ${at.slice(11, 19)}`,
    }),
  );
  return `${header}.${body}.${hexFrom(`${invoice.number}:sig`, 43)}`;
}

/** The portal's 24-hour cancellation window, as an ISO instant. */
function cancellationDeadline(ackDate: string): string {
  return new Date(Date.parse(ackDate) + 24 * 60 * 60 * 1000).toISOString();
}

/* -------------------------------- assembly -------------------------------- */

function buildLines(
  number: string,
  seeds: LineSeed[],
  ratePct: number,
  intraState: boolean,
): InvoiceLine[] {
  const codes = HSN_BY_INVOICE[number] ?? [];
  return seeds.map((line, i) => {
    const unit = paise(line.rate);
    const amount = unit * line.quantity;
    const tax = Math.round((amount * ratePct) / 100);
    // The halves are not each rounded: the total is computed once and divided, odd paisa to SGST,
    // so `cgst + sgst` equals the line's tax for every input rather than for the even ones.
    const half = Math.trunc(Math.abs(tax) / 2);
    const cgst = tax < 0 ? -half : half;
    return {
      id: `${number}-L${i + 1}`,
      description: line.description,
      quantity: line.quantity,
      unit_price_minor: unit,
      amount_minor: amount,
      hsn_sac: codes[i] ?? null,
      tax_rate_pct: ratePct,
      cgst_minor: intraState ? cgst : 0,
      sgst_minor: intraState ? tax - cgst : 0,
      igst_minor: intraState ? 0 : tax,
    };
  });
}

/** The state a supply is taxed in: the buyer's GSTIN state, or their own state when unregistered. */
function placeOfSupplyFor(customerId: string): string {
  const gstin = FIXTURE_CUSTOMERS.get(customerId)?.gstin ?? null;
  // The one unregistered buyer is in Maharashtra, the seller's own state.
  return gstin === null ? SELLER_STATE_CODE : gstin.slice(0, 2);
}

/**
 * Every recorded attempt for one document, newest first — the order the API returns them in.
 * The signed QR is attached once the invoices exist, since it quotes the document's own totals.
 */
const FILING_ROWS: ReadonlyMap<string, EInvoiceFiling[]> = new Map(
  Object.entries(FILING_SEEDS).map(([number, seeds]) => [
    number,
    seeds
      .map((seed, i): EInvoiceFiling => {
        const registered = seed.status === "filed" || seed.status === "cancelled";
        return {
          id: `fil-${number}-${i + 1}`,
          status: seed.status,
          irn: registered ? hexFrom(`${number}:irn`, 64) : null,
          ack_no: registered ? hexFrom(`${number}:ack`, 24).replace(/\D/g, "").padEnd(16, "4").slice(0, 16) : null,
          ack_date: registered ? seed.at : null,
          qr_payload: null,
          error_code: seed.error_code ?? null,
          error: seed.error ?? null,
          attempted_at: seed.at,
        };
      })
      .reverse(),
  ]),
);

/** The current e-invoice position, derived from the newest attempt exactly as the service does. */
function einvoiceState(number: string, supplyType: SupplyType): EInvoiceState {
  // B2C is the one classification that carries no obligation; everything else does, credit notes
  // included — a correction is reported to the portal as its own document.
  const required = supplyType !== "b2c";
  const history = FILING_ROWS.get(number) ?? [];
  const latest = history[0];
  if (latest === undefined) return { required, status: null, attempts: 0 };

  const cancellableUntil =
    latest.status === "filed" && latest.ack_date !== null
      ? cancellationDeadline(latest.ack_date)
      : null;
  const remedy =
    cancellableUntil === null
      ? null
      : Date.parse(cancellableUntil) > Date.parse(NOW)
        ? ("cancel" as const)
        : ("credit_note" as const);

  return {
    required,
    status: latest.status,
    irn: latest.irn,
    ack_no: latest.ack_no,
    ack_date: latest.ack_date,
    error_code: latest.error_code,
    error: latest.error,
    attempted_at: latest.attempted_at,
    attempts: history.length,
    terminal: latest.status === "failed",
    cancellable_until: cancellableUntil,
    remedy,
  };
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
  const buyerGstin = FIXTURE_CUSTOMERS.get(seed.customer_id)?.gstin ?? null;
  const supplyType: SupplyType = buyerGstin === null ? "b2c" : "b2b";
  const placeOfSupply = placeOfSupplyFor(seed.customer_id);
  const intraState = placeOfSupply === SELLER_STATE_CODE;

  const lines = buildLines(seed.number, seed.lines, seed.tax_rate_pct, intraState);
  const subtotal = lines.reduce((sum, l) => sum + l.amount_minor, 0);
  // The document's tax is the sum of its lines', head by head — never a second computation over
  // the subtotal, which is how a document ends up a paisa out from itself.
  const cgst = lines.reduce((sum, l) => sum + (l.cgst_minor ?? 0), 0);
  const sgst = lines.reduce((sum, l) => sum + (l.sgst_minor ?? 0), 0);
  const igst = lines.reduce((sum, l) => sum + (l.igst_minor ?? 0), 0);
  const tax = cgst + sgst + igst;
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
    place_of_supply: placeOfSupply,
    supply_type: supplyType,
    reverse_charge: false,
    seller_gstin: SELLER_GSTIN,
    buyer_gstin: buyerGstin,
    cgst_minor: cgst,
    sgst_minor: sgst,
    igst_minor: igst,
    einvoice: einvoiceState(seed.number, supplyType),
  };
});

const INVOICE_BY_NUMBER = new Map(INVOICES.map((inv) => [inv.number, inv]));

/**
 * The filing history the API serves, keyed by invoice id, with each registered attempt carrying
 * the signed QR that attempt produced.
 */
export const FILINGS_BY_INVOICE: ReadonlyMap<string, EInvoiceFiling[]> = new Map(
  [...FILING_ROWS].map(([number, rows]) => {
    const invoice = INVOICE_BY_NUMBER.get(number);
    return [
      `inv-${number}`,
      rows.map((row) =>
        row.irn !== null && row.ack_date !== null && invoice !== undefined
          ? { ...row, qr_payload: qrPayload(invoice, row.irn, invoice.buyer_gstin ?? null, row.ack_date) }
          : row,
      ),
    ];
  }),
);

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

/* ------------------------- IMS reconciliation queue ------------------------ *
 * The inbound half of the book: what suppliers filed against this organization, what the platform
 * suggests for each, and the evidence behind the suggestion. Every reason class appears at least
 * once, and so does every shape of decision — one that followed the suggestion, one that overruled
 * it, one parked. A queue where the machine is always right teaches nothing about the screen.  */

interface SupplierSeed {
  gstin: string;
  name: string;
}

const SUPPLIERS: Record<string, SupplierSeed> = {
  sahyadri: { gstin: "27AACCS4413K1ZP", name: "Sahyadri Alloys Pvt Ltd" },
  prakash: { gstin: "24AABCP7719L1ZB", name: "Prakash Packaging LLP" },
  nandi: { gstin: "29AAECN5521M1ZQ", name: "Nandi Freight Services" },
  maratha: { gstin: "27AAGCM8802R1ZT", name: "Maratha Fabricators" },
  vertex: { gstin: "06AAJCV1130H1ZK", name: "Vertex Toolworks" },
  southern: { gstin: "33AAKCS9042D1ZM", name: "Southern Bearings Co" },
};

function supplier(key: string): SupplierSeed {
  return SUPPLIERS[key] ?? { gstin: "00UNKNOWN0000Z0", name: "Unknown supplier" };
}

/** The tax split for an inbound document, on the same rule the sales book uses. */
function inboundSplit(taxableMinor: number, ratePct: number, gstin: string) {
  const tax = Math.round((taxableMinor * ratePct) / 100);
  const intraState = gstin.slice(0, 2) === SELLER_STATE_CODE;
  const half = Math.trunc(Math.abs(tax) / 2);
  const cgst = tax < 0 ? -half : half;
  return {
    cgst_minor: intraState ? cgst : 0,
    sgst_minor: intraState ? tax - cgst : 0,
    igst_minor: intraState ? 0 : tax,
    total_minor: taxableMinor + tax,
  };
}

interface PurchaseSeed {
  supplier: string;
  number: string;
  date: string;
  /** Taxable value in rupees. */
  taxable: number;
  rate: number;
  notes?: string;
}

/** What this organization recorded buying — the local counterpart a filing is matched against. */
const PURCHASE_SEEDS: PurchaseSeed[] = [
  { supplier: "sahyadri", number: "INV/SA/2026/1188", date: "2026-08-03", taxable: 480_000, rate: 18, notes: "Billet stock against PO-2026-0451." },
  { supplier: "prakash", number: "PP-2026-0442", date: "2026-08-05", taxable: 126_000, rate: 12 },
  { supplier: "nandi", number: "NF/AUG/0091", date: "2026-08-08", taxable: 214_000, rate: 18, notes: "Line-haul, 11 trips. Detention charges disputed." },
  { supplier: "maratha", number: "MF-2026-0310", date: "2026-08-11", taxable: 97_500, rate: 18 },
  { supplier: "maratha", number: "MF-2026-0288", date: "2026-08-19", taxable: 205_000, rate: 18 },
  { supplier: "vertex", number: "VT-2026-0701", date: "2026-07-09", taxable: 118_000, rate: 18 },
  { supplier: "prakash", number: "PP-2026-0398", date: "2026-07-28", taxable: 88_000, rate: 12 },
  { supplier: "nandi", number: "NF/JUL/0088", date: "2026-07-22", taxable: 152_000, rate: 18 },
];

export const PURCHASES: PurchaseDocument[] = PURCHASE_SEEDS.map((seed) => {
  const party = supplier(seed.supplier);
  const taxable = paise(seed.taxable);
  const split = inboundSplit(taxable, seed.rate, party.gstin);
  return {
    id: `pur-${seed.number}`,
    supplier_gstin: party.gstin,
    supplier_name: party.name,
    number: seed.number,
    document_type: "invoice",
    document_date: seed.date,
    currency: "INR",
    taxable_minor: taxable,
    tax_rate_pct: seed.rate,
    notes: seed.notes ?? null,
    ...split,
  };
});

const PURCHASE_BY_NUMBER = new Map(PURCHASES.map((p) => [p.number, p]));

interface ImsSeed {
  ref: string;
  supplier: string;
  number: string;
  date: string;
  /** Taxable value in rupees, as the *supplier* declared it. */
  taxable: number;
  rate: number;
  documentType?: ImsDocument["document_type"];
  reason: ImsReason;
  action: ImsAction;
  /** The purchase document number the reconciler matched against, where there is one. */
  matches?: string;
  /** The `ref` of the earlier arrival this one duplicates. */
  duplicateOf?: string;
  /** A decision already on file. Its absence is what "nobody has decided yet" looks like. */
  decision?: { action: ImsAction; note: string | null; by: string; at: string };
  fetchedAt: string;
}

const IMS_SEEDS: ImsSeed[] = [
  { ref: "IMS-2608-0001", supplier: "sahyadri", number: "INV/SA/2026/1188", date: "2026-08-03", taxable: 480_000, rate: 18, reason: "matched", action: "accept", matches: "INV/SA/2026/1188", fetchedAt: "2026-08-21T04:30:00Z" },
  { ref: "IMS-2608-0002", supplier: "prakash", number: "PP-2026-0442", date: "2026-08-05", taxable: 126_000, rate: 12, reason: "matched", action: "accept", matches: "PP-2026-0442", fetchedAt: "2026-08-21T04:30:00Z", decision: { action: "accept", note: null, by: "Priya Nair", at: "2026-08-21T09:12:00Z" } },
  { ref: "IMS-2608-0003", supplier: "nandi", number: "NF/AUG/0091", date: "2026-08-08", taxable: 215_000, rate: 18, reason: "amount_mismatch", action: "pending_action", matches: "NF/AUG/0091", fetchedAt: "2026-08-21T04:30:00Z" },
  { ref: "IMS-2608-0004", supplier: "maratha", number: "MF-2026-0310", date: "2026-08-11", taxable: 96_000, rate: 18, reason: "amount_mismatch", action: "pending_action", matches: "MF-2026-0310", fetchedAt: "2026-08-21T04:30:00Z", decision: { action: "pending_action", note: "Short-billed by one fabrication lot. Held pending the supplier's revised invoice.", by: "Priya Nair", at: "2026-08-21T09:26:00Z" } },
  { ref: "IMS-2608-0005", supplier: "vertex", number: "VT-2026-0777", date: "2026-08-14", taxable: 340_000, rate: 18, reason: "no_purchase_record", action: "pending_action", fetchedAt: "2026-08-21T04:30:00Z" },
  { ref: "IMS-2608-0006", supplier: "southern", number: "SB/2026/0021", date: "2026-08-16", taxable: 560_000, rate: 18, reason: "supplier_unknown", action: "reject", fetchedAt: "2026-08-21T04:30:00Z" },
  { ref: "IMS-2608-0007", supplier: "sahyadri", number: "INV/SA/2026/1188", date: "2026-08-03", taxable: 480_000, rate: 18, reason: "duplicate", action: "reject", duplicateOf: "IMS-2608-0001", fetchedAt: "2026-08-21T04:30:00Z" },
  { ref: "IMS-2608-0008", supplier: "maratha", number: "MF-2026-0288", date: "2026-08-19", taxable: 205_000, rate: 18, documentType: "credit_note", reason: "matched", action: "accept", matches: "MF-2026-0288", fetchedAt: "2026-08-21T04:30:00Z" },
  { ref: "IMS-2607-0011", supplier: "prakash", number: "PP-2026-0398", date: "2026-07-28", taxable: 88_000, rate: 12, reason: "matched", action: "accept", matches: "PP-2026-0398", fetchedAt: "2026-07-31T04:30:00Z", decision: { action: "reject", note: "Whole consignment returned on 30 Jul. Supplier to raise a credit note; the credit is not ours to claim.", by: "Devang Mehta", at: "2026-08-01T11:40:00Z" } },
  { ref: "IMS-2607-0012", supplier: "nandi", number: "NF/JUL/0088", date: "2026-07-22", taxable: 152_000, rate: 18, reason: "matched", action: "accept", matches: "NF/JUL/0088", fetchedAt: "2026-07-31T04:30:00Z", decision: { action: "accept", note: null, by: "Devang Mehta", at: "2026-08-01T11:31:00Z" } },
];

/** `action` → the status a document takes once that action is recorded. */
const ACTION_STATUS: Record<ImsAction, ImsDocument["status"]> = {
  accept: "accepted",
  reject: "rejected",
  pending_action: "pending_action",
};

/**
 * The reconciler's own sentence, in the shape the service writes it — raw paise and all.
 *
 * Deliberately not the sentence the UI shows. Keeping the fixture prose exactly as unhelpful as
 * the service's is what proves the screens do their own translating rather than leaning on
 * whatever the backend happened to phrase nicely.
 */
function suggestionDetail(seed: ImsSeed, deltaMinor: number, gstin: string): string {
  switch (seed.reason) {
    case "matched":
      return `matches purchase document ${seed.matches ?? ""} exactly, taxable value and total alike`;
    case "amount_mismatch":
      return `the supplier declares ${Math.abs(deltaMinor)} paise ${deltaMinor > 0 ? "more" : "less"} than purchase document ${seed.matches ?? ""}`;
    case "supplier_unknown":
      return `nothing has been purchased from ${gstin}; no document from this supplier is on file`;
    case "no_purchase_record":
      return `${gstin} is a known supplier, but no purchase document numbered ${seed.number} is recorded for this financial year`;
    case "duplicate":
      return `${seed.number} from ${gstin} has already been received; this is a second copy of the same document`;
  }
}

const IMS_BASE: ImsDocument[] = IMS_SEEDS.map((seed) => {
  const party = supplier(seed.supplier);
  const taxable = paise(seed.taxable);
  const split = inboundSplit(taxable, seed.rate, party.gstin);
  const matched = seed.matches !== undefined ? PURCHASE_BY_NUMBER.get(seed.matches) : undefined;
  // Signed, minor units: the supplier's total less the recorded document's.
  const delta =
    seed.reason === "amount_mismatch" && matched !== undefined
      ? split.total_minor - matched.total_minor
      : 0;

  return {
    id: `ims-${seed.ref}`,
    source_ref: seed.ref,
    source: "gstn-ims",
    supplier_gstin: party.gstin,
    supplier_name: party.name,
    document_number: seed.number,
    document_type: seed.documentType ?? "invoice",
    document_date: seed.date,
    period: seed.date.slice(0, 7),
    taxable_minor: taxable,
    irn: hexFrom(`${seed.ref}:irn`, 64),
    status: seed.decision ? ACTION_STATUS[seed.decision.action] : "pending",
    fetched_at: seed.fetchedAt,
    suggestion: {
      id: `sug-${seed.ref}`,
      suggested_action: seed.action,
      reason: seed.reason,
      detail: suggestionDetail(seed, delta, party.gstin),
      delta_minor: delta,
      matched_purchase_id: matched?.id ?? null,
      duplicate_of_id: seed.duplicateOf !== undefined ? `ims-${seed.duplicateOf}` : null,
      computed_at: seed.fetchedAt,
    },
    decision: seed.decision
      ? {
          id: `dec-${seed.ref}-1`,
          action: seed.decision.action,
          note: seed.decision.note,
          suggested_action: seed.action,
          suggestion_reason: seed.reason,
          expense_category: "general",
          decided_by: seed.decision.by,
          decided_at: seed.decision.at,
        }
      : null,
    ...split,
  };
});

/**
 * Decisions taken in this session, newest first per document.
 *
 * Append-only, like the table it stands in for: a change of mind is a further entry and the one
 * it superseded stays visible underneath it. Fixture mode is the only writer.
 */
const SESSION_DECISIONS = new Map<string, ImsDecision[]>();

function decisionsFor(document: ImsDocument): ImsDecision[] {
  const session = SESSION_DECISIONS.get(document.id) ?? [];
  const seeded = document.decision ? [document.decision] : [];
  return [...session, ...seeded];
}

/** The queue as it currently stands, with any decision taken in this session applied. */
export function fixtureImsDocuments(): ImsDocument[] {
  return IMS_BASE.map((document) => {
    const decisions = decisionsFor(document);
    const newest = decisions[0];
    if (newest === undefined) return document;
    return { ...document, status: ACTION_STATUS[newest.action], decision: newest };
  });
}

/** One inbound document with the evidence behind its suggestion and its whole decision history. */
export function fixtureImsDetail(id: string): ImsDocumentDetail | null {
  // The history is read off the *seed* row, not the overlaid one: the overlay has already folded
  // the newest decision into `decision`, and asking it for its history again would count that
  // decision twice.
  const base = IMS_BASE.find((d) => d.id === id);
  const document = fixtureImsDocuments().find((d) => d.id === id);
  if (base === undefined || document === undefined) return null;
  const suggestion = document.suggestion ?? null;
  const matched =
    suggestion?.matched_purchase_id != null
      ? (PURCHASES.find((p) => p.id === suggestion.matched_purchase_id) ?? null)
      : null;
  const duplicate =
    suggestion?.duplicate_of_id != null
      ? (IMS_BASE.find((d) => d.id === suggestion.duplicate_of_id) ?? null)
      : null;
  return {
    document,
    matched_purchase: matched,
    duplicate_of: duplicate,
    decisions: decisionsFor(base),
  };
}

/** Record one decision against one document. Appends; never overwrites the last one. */
export function recordFixtureDecision(
  documentId: string,
  action: ImsAction,
  note: string | null,
  expenseCategory: ImsExpenseCategory,
  decidedBy: string,
): ImsDecision {
  const document = IMS_BASE.find((d) => d.id === documentId);
  const suggestion = document?.suggestion ?? null;
  const decision: ImsDecision = {
    id: `dec-${documentId}-${(SESSION_DECISIONS.get(documentId)?.length ?? 0) + 2}`,
    action,
    note,
    suggested_action: suggestion?.suggested_action ?? null,
    suggestion_reason: suggestion?.reason ?? null,
    expense_category: expenseCategory,
    decided_by: decidedBy,
    decided_at: new Date().toISOString(),
  };
  SESSION_DECISIONS.set(documentId, [decision, ...(SESSION_DECISIONS.get(documentId) ?? [])]);
  return decision;
}

/* --------------------------- GST registration ------------------------------ */

export const GST_REGISTRATION: GstRegistration = {
  id: "reg-fixture-01",
  gstin: SELLER_GSTIN,
  state_code: SELLER_STATE_CODE,
  state_name: "Maharashtra",
  legal_name: "OpsGrids Demo Trading Pvt Ltd",
  trade_name: "OpsGrids Demo",
  registration_type: "regular",
  is_primary: true,
  address_line1: "Plot 14, MIDC Bhosari",
  address_line2: "",
  city: "Pune",
  pincode: "411026",
};

/** PAN and bank details as the settings read returns them: account number masked. */
export const SUPPLIER_PROFILE: SupplierSettings["profile"] = {
  pan: "AABCA9012D",
  bank: {
    account_name: "OpsGrids Demo Trading Pvt Ltd",
    account_number: "••••••••••6789",
    ifsc: "HDFC0001234",
    bank_name: "HDFC Bank",
    branch: "Bhosari",
    upi_id: "opsgrids@hdfcbank",
  },
};

/** The supplier settings read, computed from the fixture registration the way the server does. */
export function fixtureSupplierSettings(): SupplierSettings {
  const r = GST_REGISTRATION;
  const missing: SupplierField[] = [];
  if (r.legal_name.trim() === "") missing.push("legal_name");
  if ((r.address_line1 ?? "").trim() === "") missing.push("address_line1");
  if ((r.city ?? "").trim() === "") missing.push("city");
  if (!/^[1-9]\d{5}$/.test(r.pincode ?? "")) missing.push("pincode");
  return { registration: r, profile: SUPPLIER_PROFILE, ready: missing.length === 0, missing };
}

/* --------------------------------- GST returns ------------------------------ */

export const RETURNS: GstReturn[] = [
  {
    id: "ret-2026-07-gstr1",
    gst_registration_id: GST_REGISTRATION.id,
    gstin: SELLER_GSTIN,
    state_code: SELLER_STATE_CODE,
    period: "2026-07",
    return_type: "gstr1",
    status: "filed",
    total_taxable_minor: paise(2_140_000),
    total_cgst_minor: paise(96_000),
    total_sgst_minor: paise(96_000),
    total_igst_minor: paise(72_000),
    document_count: 6,
    line_count: 9,
    generated_at: "2026-08-02T09:10:00Z",
    filed_at: "2026-08-05T07:44:00Z",
    arn: "AA270826001234A",
  },
  {
    id: "ret-2026-08-gstr1",
    gst_registration_id: GST_REGISTRATION.id,
    gstin: SELLER_GSTIN,
    state_code: SELLER_STATE_CODE,
    period: "2026-08",
    return_type: "gstr1",
    status: "generated",
    total_taxable_minor: paise(2_980_000),
    total_cgst_minor: paise(118_000),
    total_sgst_minor: paise(118_000),
    total_igst_minor: paise(94_000),
    document_count: 8,
    line_count: 12,
    generated_at: "2026-08-24T06:00:00Z",
    filed_at: null,
    arn: null,
  },
  {
    id: "ret-2026-07-gstr3b",
    gst_registration_id: GST_REGISTRATION.id,
    gstin: SELLER_GSTIN,
    state_code: SELLER_STATE_CODE,
    period: "2026-07",
    return_type: "gstr3b",
    status: "filed",
    // Table 3.1's totals — must, and do, match the July GSTR-1 above.
    total_taxable_minor: paise(2_140_000),
    total_cgst_minor: paise(96_000),
    total_sgst_minor: paise(96_000),
    total_igst_minor: paise(72_000),
    document_count: 6,
    line_count: 9,
    generated_at: "2026-08-03T08:00:00Z",
    filed_at: "2026-08-05T07:50:00Z",
    arn: "AB270826005678B",
  },
];

export const RETURN_TRANSITIONS: Record<string, GstReturnTransition[]> = {
  "ret-2026-07-gstr1": [
    { id: "trn-01", from_status: null, to_status: "generated", arn: null, note: null, total_taxable_minor: paise(2_140_000), total_tax_minor: paise(264_000), transitioned_by: "Rahul Deshpande", transitioned_at: "2026-08-02T09:10:00Z" },
    { id: "trn-02", from_status: "generated", to_status: "filed", arn: "AA270826001234A", note: "Filed on schedule.", total_taxable_minor: paise(2_140_000), total_tax_minor: paise(264_000), transitioned_by: "Rahul Deshpande", transitioned_at: "2026-08-05T07:44:00Z" },
  ],
  "ret-2026-08-gstr1": [
    { id: "trn-03", from_status: null, to_status: "generated", arn: null, note: null, total_taxable_minor: paise(2_980_000), total_tax_minor: paise(330_000), transitioned_by: "Rahul Deshpande", transitioned_at: "2026-08-24T06:00:00Z" },
  ],
  "ret-2026-07-gstr3b": [
    { id: "trn-04", from_status: null, to_status: "generated", arn: null, note: null, total_taxable_minor: paise(2_140_000), total_tax_minor: paise(264_000), transitioned_by: "Rahul Deshpande", transitioned_at: "2026-08-03T08:00:00Z" },
    { id: "trn-05", from_status: "generated", to_status: "filed", arn: "AB270826005678B", note: "Filed after the ITC ledger was reconciled.", total_taxable_minor: paise(2_140_000), total_tax_minor: paise(264_000), transitioned_by: "Rahul Deshpande", transitioned_at: "2026-08-05T07:50:00Z" },
  ],
};

const ZERO_HEADS: TaxHeadAmounts = {
  igst_minor: 0,
  cgst_minor: 0,
  sgst_minor: 0,
  cess_minor: 0,
  total_minor: 0,
};

/**
 * July's GSTR-3B. Table 3.1's liability — 96,000 CGST + 96,000 SGST + 72,000 IGST — is exactly
 * July's GSTR-1 totals above, the reconciliation the finance service checks at generation time.
 *
 * The ITC side: ₹90,000 of fresh IGST credit this period (4A(5)), ₹4,000 CGST + ₹4,000 SGST that
 * would have been credit but was blocked under s. 17(5) — a motor-vehicle purchase, shown for
 * transparency in 4D(1) and never added to the balance — and ₹1,80,000 of IGST brought forward
 * from June. IGST credit is exhausted first per the statutory order: it clears its own liability,
 * then spills into CGST, then SGST, leaving ₹6,000 of IGST credit carried into August and nothing
 * payable in cash.
 */
const GSTR3B_SECTIONS: Gstr3bSections = {
  outward: {
    taxable_supplies: {
      igst_minor: paise(72_000),
      cgst_minor: paise(96_000),
      sgst_minor: paise(96_000),
      cess_minor: 0,
      total_minor: paise(264_000),
    },
    outward_taxable_minor: paise(2_140_000),
    zero_rated: ZERO_HEADS,
    zero_rated_taxable_minor: 0,
    nil_rated_taxable_minor: 0,
    reverse_charge_inward_taxable_minor: 0,
    non_gst_taxable_minor: 0,
    outward_reverse_charge: ZERO_HEADS,
    outward_reverse_charge_taxable_minor: 0,
  },
  itc: {
    // 4A(5) gross: the IGST actually accrued, plus the CGST+SGST that would have but for being
    // blocked under s. 17(5) (a motor-vehicle purchase).
    available: {
      igst_minor: paise(90_000),
      cgst_minor: paise(4_000),
      sgst_minor: paise(4_000),
      cess_minor: 0,
      total_minor: paise(98_000),
    },
    reversed_section_17_5: {
      igst_minor: 0,
      cgst_minor: paise(4_000),
      sgst_minor: paise(4_000),
      cess_minor: 0,
      total_minor: paise(8_000),
    },
    reversed_other: ZERO_HEADS,
    reclaimed: ZERO_HEADS,
    ineligible_other: ZERO_HEADS,
    total_reversed: {
      igst_minor: 0,
      cgst_minor: paise(4_000),
      sgst_minor: paise(4_000),
      cess_minor: 0,
      total_minor: paise(8_000),
    },
    net_eligible: { igst_minor: paise(90_000), cgst_minor: 0, sgst_minor: 0, cess_minor: 0, total_minor: paise(90_000) },
  },
  itc_brought_forward: {
    igst_minor: paise(180_000),
    cgst_minor: 0,
    sgst_minor: 0,
    cess_minor: 0,
    total_minor: paise(180_000),
  },
  payment: {
    liability: {
      igst_minor: paise(72_000),
      cgst_minor: paise(96_000),
      sgst_minor: paise(96_000),
      cess_minor: 0,
      total_minor: paise(264_000),
    },
    utilisation: [
      { head: "igst", against: "igst", amount_minor: paise(72_000) },
      { head: "igst", against: "cgst", amount_minor: paise(96_000) },
      { head: "igst", against: "sgst", amount_minor: paise(96_000) },
    ],
    credit_remaining: { igst_minor: paise(6_000), cgst_minor: 0, sgst_minor: 0, cess_minor: 0, total_minor: paise(6_000) },
    cash_payable: ZERO_HEADS,
    excess_liability: ZERO_HEADS,
  },
};

const EMPTY_TOTALS: ReturnTotals = {
  taxable_minor: 0,
  cgst_minor: 0,
  sgst_minor: 0,
  igst_minor: 0,
  tax_minor: 0,
  total_minor: 0,
  document_count: 0,
  line_count: 0,
};

export function fixtureReturnDetail(id: string): GstReturnDetail | null {
  const ret = RETURNS.find((r) => r.id === id);
  if (!ret) return null;
  return {
    ret,
    sections:
      ret.return_type === "gstr3b"
        ? GSTR3B_SECTIONS
        : { b2b: [], b2cl: [], b2cs: [], cdnr: [], cdnur: [], exp: [], hsn: [], docs: [] },
    transitions: RETURN_TRANSITIONS[id] ?? [],
  };
}

export function fixtureReturnDiff(id: string): ReturnDiff | null {
  const ret = RETURNS.find((r) => r.id === id);
  if (!ret) return null;
  const totals: ReturnTotals = {
    ...EMPTY_TOTALS,
    taxable_minor: ret.total_taxable_minor,
    cgst_minor: ret.total_cgst_minor,
    sgst_minor: ret.total_sgst_minor,
    igst_minor: ret.total_igst_minor,
    tax_minor: ret.total_cgst_minor + ret.total_sgst_minor + ret.total_igst_minor,
    total_minor: ret.total_taxable_minor + ret.total_cgst_minor + ret.total_sgst_minor + ret.total_igst_minor,
    document_count: ret.document_count,
    line_count: ret.line_count,
  };
  return {
    ret,
    current: totals,
    snapshot: totals,
    delta: EMPTY_TOTALS,
    sections: [],
    changed: false,
    computed_at: NOW,
  };
}

/* ------------------------------- e-Way bills --------------------------------- */

export const EWAY_BILLS: EwayBillWithInvoice[] = [
  {
    bill: {
      id: "eway-01",
      invoice_id: "inv-INV-2026-0163",
      status: "generated",
      effective_status: "generated",
      eway_bill_no: "351009876543",
      transport_mode: "road",
      distance_km: 420,
      vehicle_no: "MH12AB1234",
      transporter_id: null,
      transport_doc_no: "LR-88214",
      transport_doc_date: "2026-07-28",
      over_dimensional: false,
      consignment_value_minor: paise(4_366_000),
      valid_from: "2026-07-28T09:20:00Z",
      valid_until: "2026-07-30T09:20:00Z",
      remedy: null,
      error_code: null,
      error: null,
      attempted_at: "2026-07-28T09:20:00Z",
    },
    invoice_number: "INV-2026-0163",
    customer_name: "Trishul Steel Works",
  },
];

/* ------------------------------ withholding: TDS / TCS ----------------------- */

export const WITHHOLDING_RATES: WithholdingRate[] = [
  { id: "wr-01", code: "gst_tds_51", name: "GST TDS — s.51", rate_pct: 2, threshold_minor: paise(250_000), basis: "supply", jurisdiction: "IN", effective_from: "2018-10-01", effective_to: null },
  { id: "wr-02", code: "itax_194q", name: "Income tax TDS — s.194Q", rate_pct: 0.1, threshold_minor: paise(5_000_000), basis: "aggregate", jurisdiction: "IN", effective_from: "2021-07-01", effective_to: null },
  { id: "wr-03", code: "itax_206c_1h", name: "Income tax TCS — s.206C(1H)", rate_pct: 0.1, threshold_minor: paise(5_000_000), basis: "aggregate", jurisdiction: "IN", effective_from: "2020-10-01", effective_to: null },
];

export const WITHHOLDINGS: WithholdingWithInvoice[] = [];

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
