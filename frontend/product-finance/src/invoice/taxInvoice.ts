/**
 * The printable GST tax invoice, as data.
 *
 * `buildTaxInvoice` turns what the API returned (the invoice, its buyer, the seller's
 * registration, the e-invoice filing and, on a credit note, the invoice it corrects) into exactly
 * what the document prints, plus a list of **gaps**: required facts the data does not carry.
 *
 * A gap is never papered over on the page. The document omits the field, and the screen around it
 * says what is missing and where to put it. No placeholder text reaches paper.
 *
 * Pure: no I/O, no formatting. Money stays in integer minor units end to end.
 */

import type {
  EInvoiceFiling,
  Customer,
  GstRegistration,
  Invoice,
  InvoiceLine,
  SupplyType,
} from "@/api/types";
import { GST_STATE_NAME } from "@/data/gstStates";
import type { Minor } from "@/lib/money";

export type GapSeverity = "blocking" | "advisory";

export type GapKey =
  | "supplier_registration"
  | "supplier_gstin"
  | "supplier_mismatch"
  | "place_of_supply"
  | "buyer_gstin"
  | "hsn_sac"
  | "irn_missing"
  | "portal_simulated"
  | "qr_missing"
  | "original_invoice"
  | "not_issued";

/** A required fact the document cannot state, and where it is fixed (an in-app route). */
export interface DocumentGap {
  key: GapKey;
  severity: GapSeverity;
  message: string;
  fix?: { label: string; to: string };
}

/** The tax heads a document charges: CGST and SGST together within a state, IGST across states. */
export type TaxMode = "intra" | "inter";

export interface TaxInvoiceLine {
  number: number;
  description: string;
  hsnSac: string | null;
  quantity: number;
  ratePerUnit: Minor;
  taxable: Minor;
  taxRatePct: number;
  cgst: Minor;
  sgst: Minor;
  igst: Minor;
  /** Taxable value plus this line's tax. */
  lineTotal: Minor;
}

/** One row of the tax summary: lines that share an HSN/SAC and a rate, added together. */
export interface TaxSummaryRow {
  hsnSac: string | null;
  taxRatePct: number;
  taxable: Minor;
  cgst: Minor;
  sgst: Minor;
  igst: Minor;
  totalTax: Minor;
}

export interface TaxInvoiceParty {
  name: string;
  /** City and state as the customer record holds them; there is no street address on file. */
  location: string | null;
  gstin: string | null;
  stateName: string | null;
  stateCode: string | null;
  contact: string | null;
}

export interface TaxInvoiceSupplier {
  /** The name set largest: trade name when the registration has one, else the legal name. */
  displayName: string | null;
  legalName: string | null;
  gstin: string | null;
  stateName: string | null;
  stateCode: string | null;
}

export interface TaxInvoiceEInvoice {
  irn: string;
  ackNo: string | null;
  ackDate: string | null;
  qrPayload: string | null;
}

export interface TaxInvoiceModel {
  kind: "invoice" | "credit_note";
  title: string;
  number: string;
  issuedDate: string;
  dueDate: string;
  /** Only meaningful on a lifecycle that is not the ordinary issued one. */
  lifecycleMark: "Draft" | "Void" | null;
  supplyLabel: string;
  reverseCharge: boolean;
  placeOfSupply: { name: string | null; code: string } | null;
  supplier: TaxInvoiceSupplier;
  buyer: TaxInvoiceParty;
  /** The invoice a credit note corrects. */
  corrects: { number: string; issuedDate: string } | null;
  currency: string;
  taxMode: TaxMode;
  lines: TaxInvoiceLine[];
  /** True when at least one line carries an HSN/SAC; otherwise the column is not printed. */
  showHsn: boolean;
  summary: TaxSummaryRow[];
  totals: {
    taxable: Minor;
    cgst: Minor;
    sgst: Minor;
    igst: Minor;
    totalTax: Minor;
    total: Minor;
  };
  /** Null unless the invoice is in INR: the words are Indian-rupee words. */
  amountInWordsMinor: Minor | null;
  einvoice: TaxInvoiceEInvoice | null;
  notes: string | null;
}

export interface TaxInvoiceInputs {
  invoice: Invoice;
  customer: Customer;
  /** The seller's registration on file; null when the organization has none. */
  registration: GstRegistration | null;
  /** The invoice a credit note corrects. Null for an ordinary invoice. */
  original: Invoice | null;
  /** The invoice's filing history, newest first as the API serves it; null when not loaded. */
  filings: readonly EInvoiceFiling[] | null;
  /**
   * Whether e-invoicing reaches the real GST portal. While it does not, an IRN or QR on file came
   * from a simulator, and printing one on a document sent to a customer would present an
   * unregistered number as a government-issued one. They are left off.
   */
  portalConnected: boolean;
}

const SUPPLY_LABEL: Record<SupplyType, string> = {
  b2b: "Business to business (B2B)",
  b2c: "Business to consumer (B2C)",
  export: "Export",
  sez: "Supply to a Special Economic Zone (SEZ)",
};

const SETTINGS_FIX = { label: "Open GST registration settings", to: "/settings" } as const;

/** A credit note is stored negative; the document states magnitudes and the title says which way. */
const mag = (n: Minor | undefined): Minor => Math.abs(n ?? 0);

function stateOf(code: string | null): string | null {
  return code ? (GST_STATE_NAME[code] ?? null) : null;
}

/** The tax heads for a document: whichever was actually charged, else by the two states. */
function taxModeFor(invoice: Invoice, sellerState: string | null): TaxMode {
  if (mag(invoice.igst_minor) > 0) return "inter";
  if (mag(invoice.cgst_minor) > 0 || mag(invoice.sgst_minor) > 0) return "intra";
  if (invoice.supply_type === "export" || invoice.supply_type === "sez") return "inter";
  const pos = invoice.place_of_supply ?? null;
  return sellerState !== null && pos !== null && sellerState !== pos ? "inter" : "intra";
}

function toLine(line: InvoiceLine, index: number, invoiceRate: number): TaxInvoiceLine {
  const cgst = mag(line.cgst_minor);
  const sgst = mag(line.sgst_minor);
  const igst = mag(line.igst_minor);
  const taxable = mag(line.amount_minor);
  return {
    number: index + 1,
    description: line.description,
    hsnSac: line.hsn_sac ?? null,
    quantity: Math.abs(line.quantity),
    ratePerUnit: mag(line.unit_price_minor),
    taxable,
    taxRatePct: line.tax_rate_pct ?? invoiceRate,
    cgst,
    sgst,
    igst,
    lineTotal: taxable + cgst + sgst + igst,
  };
}

/**
 * Group lines by HSN/SAC and rate, the way a GST return's HSN summary does. Sorted by code then
 * rate so the same invoice always prints the same table. Lines without a code group together
 * under `null` rather than being dropped, so the summary always foots to the lines.
 */
export function summariseTax(lines: readonly TaxInvoiceLine[]): TaxSummaryRow[] {
  const groups = new Map<string, TaxSummaryRow>();
  for (const line of lines) {
    const key = `${line.hsnSac ?? ""}|${line.taxRatePct}`;
    const row = groups.get(key) ?? {
      hsnSac: line.hsnSac,
      taxRatePct: line.taxRatePct,
      taxable: 0,
      cgst: 0,
      sgst: 0,
      igst: 0,
      totalTax: 0,
    };
    row.taxable += line.taxable;
    row.cgst += line.cgst;
    row.sgst += line.sgst;
    row.igst += line.igst;
    row.totalTax += line.cgst + line.sgst + line.igst;
    groups.set(key, row);
  }
  return [...groups.values()].sort(
    (a, b) => (a.hsnSac ?? "").localeCompare(b.hsnSac ?? "") || a.taxRatePct - b.taxRatePct,
  );
}

/** The registered attempt that carries the signed QR, if any. */
export function registeredFiling(filings: readonly EInvoiceFiling[] | null): EInvoiceFiling | null {
  return filings?.find((f) => f.status === "filed" && f.qr_payload !== null) ?? null;
}

export function buildTaxInvoice(input: TaxInvoiceInputs): {
  model: TaxInvoiceModel;
  gaps: DocumentGap[];
} {
  const { invoice, customer, registration, original, filings, portalConnected } = input;
  const gaps: DocumentGap[] = [];
  const isCreditNote = invoice.document_type === "credit_note";

  // --- Supplier. The GSTIN the invoice was raised under is the legal fact; the registration only
  // supplies the names, and only when it is the same registration.
  const sellerGstin = invoice.seller_gstin ?? registration?.gstin ?? null;
  const registrationMatches =
    registration !== null && (invoice.seller_gstin == null || invoice.seller_gstin === registration.gstin);
  const sellerStateCode = sellerGstin ? sellerGstin.slice(0, 2) : (registration?.state_code ?? null);

  if (registration === null) {
    gaps.push({
      key: "supplier_registration",
      severity: "blocking",
      message:
        "Your GST registration is not set up, so the invoice cannot state the supplier's legal name. Add it in Settings, then come back.",
      fix: SETTINGS_FIX,
    });
  } else if (!registrationMatches) {
    gaps.push({
      key: "supplier_mismatch",
      severity: "blocking",
      message: `This invoice was raised under GSTIN ${invoice.seller_gstin ?? ""}, but the registration on file is ${registration.gstin}. The supplier's legal name for the invoice's GSTIN is not available.`,
      fix: SETTINGS_FIX,
    });
  } else if (registration.legal_name.trim() === "") {
    gaps.push({
      key: "supplier_registration",
      severity: "blocking",
      message: "Your GST registration has no legal name. Add it in Settings, then come back.",
      fix: SETTINGS_FIX,
    });
  }
  if (sellerGstin === null) {
    gaps.push({
      key: "supplier_gstin",
      severity: "blocking",
      message:
        "This invoice carries no supplier GSTIN, and no registration is on file to supply one. A tax invoice must state it.",
      fix: SETTINGS_FIX,
    });
  }

  const usable = registrationMatches ? registration : null;
  const legalName = usable && usable.legal_name.trim() !== "" ? usable.legal_name.trim() : null;
  const tradeName = usable && usable.trade_name.trim() !== "" ? usable.trade_name.trim() : null;

  // --- Place of supply and tax heads.
  const pos = invoice.place_of_supply ?? null;
  if (pos === null) {
    gaps.push({
      key: "place_of_supply",
      severity: "blocking",
      message:
        "This invoice was raised before a place of supply was recorded, so the document cannot state it. Issue a credit note and re-issue it with the registration configured.",
    });
  }

  // --- Buyer.
  const supplyType = invoice.supply_type ?? "b2b";
  const buyerGstin = invoice.buyer_gstin ?? null;
  if (supplyType === "b2b" && buyerGstin === null) {
    gaps.push({
      key: "buyer_gstin",
      severity: "advisory",
      message: `${customer.name} has no GSTIN on this invoice, so the buyer's GSTIN is left off. The buyer cannot claim input tax credit without it.`,
      fix: { label: "Open customers", to: "/customers" },
    });
  }
  const buyerStateCode = buyerGstin ? buyerGstin.slice(0, 2) : null;
  const location = [customer.city, customer.state].map((s) => s.trim()).filter(Boolean).join(", ");

  // --- Lines.
  const invoiceRate = invoice.tax_rate_pct;
  const lines = invoice.lines.map((l, i) => toLine(l, i, invoiceRate));
  const showHsn = lines.some((l) => l.hsnSac !== null);
  const missingHsn = lines.filter((l) => l.hsnSac === null).length;
  if (missingHsn > 0) {
    gaps.push({
      key: "hsn_sac",
      severity: "advisory",
      message:
        missingHsn === lines.length
          ? "No line on this invoice has an HSN or SAC code, so the column is left off."
          : `${missingHsn} of ${lines.length} lines have no HSN or SAC code and are left blank.`,
    });
  }

  // --- Credit note reference.
  if (isCreditNote && original === null) {
    gaps.push({
      key: "original_invoice",
      severity: "blocking",
      message: "The invoice this credit note corrects could not be loaded, so the reference is missing.",
    });
  }

  // --- E-invoice.
  const filing = registeredFiling(filings);
  const state = invoice.einvoice;
  const irn = filing?.irn ?? state?.irn ?? null;
  let einvoice: TaxInvoiceEInvoice | null = null;
  if (!portalConnected) {
    gaps.push({
      key: "portal_simulated",
      severity: "advisory",
      message:
        "E-invoicing is not connected to the GST portal yet, so this copy carries no IRN, acknowledgement or QR code. Any IRN shown in Finance comes from a simulator and is not printed. If this invoice needs an e-invoice, it is not valid until it is registered.",
    });
  } else if (irn !== null) {
    einvoice = {
      irn,
      ackNo: filing?.ack_no ?? state?.ack_no ?? null,
      ackDate: filing?.ack_date ?? state?.ack_date ?? null,
      qrPayload: filing?.qr_payload ?? null,
    };
    if (einvoice.qrPayload === null) {
      gaps.push({
        key: "qr_missing",
        severity: "blocking",
        message:
          "This invoice is registered with the e-invoice portal but its signed QR code could not be loaded. An e-invoice must carry it.",
      });
    }
  } else if (state?.required === true) {
    gaps.push({
      key: "irn_missing",
      severity: "advisory",
      message:
        "This invoice needs an IRN and has not been registered with the e-invoice portal yet. A copy without one is not a valid e-invoice.",
    });
  }

  if (invoice.status === "draft") {
    gaps.push({
      key: "not_issued",
      severity: "advisory",
      message: "This invoice is a draft and has not been issued. The copy is marked Draft.",
    });
  } else if (invoice.status === "void") {
    gaps.push({
      key: "not_issued",
      severity: "advisory",
      message: "This invoice is void. The copy is marked Void.",
    });
  }

  const totalsTaxable = mag(invoice.subtotal_minor);
  const cgst = mag(invoice.cgst_minor);
  const sgst = mag(invoice.sgst_minor);
  const igst = mag(invoice.igst_minor);

  const model: TaxInvoiceModel = {
    kind: isCreditNote ? "credit_note" : "invoice",
    title: isCreditNote ? "Credit Note" : "Tax Invoice",
    number: invoice.number,
    issuedDate: invoice.issued_date,
    dueDate: invoice.due_date,
    lifecycleMark: invoice.status === "draft" ? "Draft" : invoice.status === "void" ? "Void" : null,
    supplyLabel: SUPPLY_LABEL[supplyType],
    reverseCharge: invoice.reverse_charge === true,
    placeOfSupply: pos === null ? null : { name: stateOf(pos), code: pos },
    supplier: {
      displayName: tradeName ?? legalName,
      legalName: tradeName !== null && legalName !== null && tradeName !== legalName ? legalName : null,
      gstin: sellerGstin,
      stateName: stateOf(sellerStateCode),
      stateCode: sellerStateCode,
    },
    buyer: {
      name: customer.name,
      location: location === "" ? null : location,
      gstin: buyerGstin,
      stateName: stateOf(buyerStateCode),
      stateCode: buyerStateCode,
      contact: customer.contact_name.trim() === "" ? null : customer.contact_name.trim(),
    },
    corrects: original === null ? null : { number: original.number, issuedDate: original.issued_date },
    currency: invoice.currency,
    taxMode: taxModeFor(invoice, sellerStateCode),
    lines,
    showHsn,
    summary: summariseTax(lines),
    totals: {
      taxable: totalsTaxable,
      cgst,
      sgst,
      igst,
      totalTax: mag(invoice.tax_minor),
      total: mag(invoice.total_minor),
    },
    amountInWordsMinor: invoice.currency === "INR" ? mag(invoice.total_minor) : null,
    einvoice,
    notes: invoice.notes?.trim() ? invoice.notes.trim() : null,
  };

  return { model, gaps };
}

/** True when nothing stops the document from being downloaded or printed. */
export function isPrintable(gaps: readonly DocumentGap[]): boolean {
  return gaps.every((g) => g.severity !== "blocking");
}

/** The file name a browser offers for the saved PDF: `INV-2026-0482 - Customer`. */
export function documentFileTitle(number: string, partyName: string): string {
  const clean = (s: string) =>
    [...s]
      .map((ch) => (ch.charCodeAt(0) < 32 || '\\/:*?"<>|'.includes(ch) ? "-" : ch))
      .join("")
      .replace(/\s+/g, " ")
      .trim();
  return `${clean(number)} - ${clean(partyName)}`;
}
