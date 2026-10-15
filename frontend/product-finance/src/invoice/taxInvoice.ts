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
  BankDetails,
  EInvoiceFiling,
  Customer,
  GstRegistration,
  Invoice,
  InvoiceLine,
  SupplierPrintDetails,
  SupplyType,
} from "@/api/types";
import { GST_STATE_NAME } from "@/data/gstStates";
import type { Minor } from "@/lib/money";

export type GapSeverity = "blocking" | "advisory";

export type GapKey =
  | "supplier_registration"
  | "supplier_gstin"
  | "supplier_mismatch"
  | "supplier_address"
  | "place_of_supply"
  | "buyer_gstin"
  | "buyer_address"
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
  /** GST unit quantity code; null on lines raised before units were recorded. */
  unit: string | null;
  ratePerUnit: Minor;
  discount: Minor;
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
  /** The billing address, one printed line each; empty when none is on file. */
  addressLines: string[];
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
  /** The principal place of business, one printed line each. */
  addressLines: string[];
  pan: string | null;
  /** Where to pay; null when no account is on file or it could not be loaded. */
  bank: BankDetails | null;
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
  /** The delivery address, when it differs from the billing address (Rule 46(n)). */
  shipToLines: string[];
  /** The invoice a credit note corrects. */
  corrects: { number: string; issuedDate: string } | null;
  currency: string;
  taxMode: TaxMode;
  lines: TaxInvoiceLine[];
  /** True when at least one line carries an HSN/SAC; otherwise the column is not printed. */
  showHsn: boolean;
  /** True when any line carries a discount; otherwise the column is not printed. */
  showDiscount: boolean;
  summary: TaxSummaryRow[];
  totals: {
    taxable: Minor;
    cgst: Minor;
    sgst: Minor;
    igst: Minor;
    totalTax: Minor;
    roundOff: Minor;
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
   * The live supplier block (address, PAN, bank account whole); null when it could not be read.
   * Used only when it is the registration the invoice was issued under.
   */
  printDetails?: SupplierPrintDetails | null;
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
    unit: line.unit ?? null,
    ratePerUnit: mag(line.unit_price_minor),
    discount: mag(line.discount_minor),
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

/** Rule 46(e): an unregistered recipient's address is mandatory from ₹50,000 of taxable value. */
const B2C_ADDRESS_THRESHOLD_MINOR = 50_000_00;

function lines(text: string | null | undefined): string[] {
  return (text ?? "")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
}

function addressLinesOf(parts: {
  address_line1?: string | undefined;
  address_line2?: string | undefined;
  city?: string | undefined;
  pincode?: string | undefined;
}): string[] {
  const cityLine = [parts.city, parts.pincode].map((s) => (s ?? "").trim()).filter(Boolean).join(" ");
  return [parts.address_line1, parts.address_line2, cityLine].map((s) => (s ?? "").trim()).filter(Boolean);
}

export function buildTaxInvoice(input: TaxInvoiceInputs): {
  model: TaxInvoiceModel;
  gaps: DocumentGap[];
} {
  const { invoice, customer, registration, original, filings, portalConnected } = input;
  const printDetails = input.printDetails ?? null;
  const gaps: DocumentGap[] = [];
  const isCreditNote = invoice.document_type === "credit_note";
  // What the document froze about its supplier at issue wins over anything live.
  const frozenLegalName = invoice.seller_legal_name?.trim() ? invoice.seller_legal_name.trim() : null;

  // --- Supplier. The GSTIN the invoice was raised under is the legal fact; the registration only
  // supplies the names, and only when it is the same registration.
  const sellerGstin = invoice.seller_gstin ?? registration?.gstin ?? null;
  const registrationMatches =
    registration !== null && (invoice.seller_gstin == null || invoice.seller_gstin === registration.gstin);
  const sellerStateCode = sellerGstin ? sellerGstin.slice(0, 2) : (registration?.state_code ?? null);

  if (frozenLegalName !== null) {
    // The legal name was frozen onto the document at issue; nothing live can contradict it.
  } else if (registration === null) {
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
  const legalName = frozenLegalName ?? (usable && usable.legal_name.trim() !== "" ? usable.legal_name.trim() : null);
  const tradeName = usable && usable.trade_name.trim() !== "" ? usable.trade_name.trim() : null;
  // The live supplier block is used only when it is the registration the invoice names.
  const livePrint = printDetails !== null && printDetails.gstin !== null && printDetails.gstin === sellerGstin ? printDetails : null;
  // A document issued before party snapshots existed (no `seller_legal_name` and no
  // `seller_address` frozen on it) reprints with what it carried then: no address is filled in
  // from today's registration, and the server returns no PAN or bank block for it either.
  const issuedBeforeSnapshots = invoice.seller_legal_name == null && invoice.seller_address == null;
  const supplierAddress = invoice.seller_address
    ? lines(invoice.seller_address)
    : issuedBeforeSnapshots
      ? []
      : livePrint
        ? addressLinesOf(livePrint)
        : usable
          ? addressLinesOf(usable)
          : [];
  // With no registration at all, the registration gap above already sends the person to the
  // one form that holds the address too; a second alert would only repeat it. A document issued
  // since snapshots must state its address, so its gap blocks until the address is added.
  if (
    !issuedBeforeSnapshots &&
    supplierAddress.length === 0 &&
    sellerGstin !== null &&
    (registration !== null || frozenLegalName !== null)
  ) {
    gaps.push({
      key: "supplier_address",
      severity: "blocking",
      message:
        "Your registered address is not on file, and a tax invoice must state it. Add it to your GST registration in Settings, then come back.",
      fix: SETTINGS_FIX,
    });
  }

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
  const buyerStateCode =
    invoice.buyer_state_code ?? (buyerGstin ? buyerGstin.slice(0, 2) : (customer.state_code ?? null));
  const buyerAddress = invoice.buyer_address
    ? lines(invoice.buyer_address)
    : addressLinesOf({
        address_line1: customer.address_line1,
        address_line2: customer.address_line2,
        city: customer.city,
        pincode: customer.pincode,
      });
  if (
    buyerGstin === null &&
    buyerAddress.length === 0 &&
    mag(invoice.subtotal_minor) >= B2C_ADDRESS_THRESHOLD_MINOR
  ) {
    gaps.push({
      key: "buyer_address",
      severity: "advisory",
      message: `${customer.name} is unregistered and billed ₹50,000 or more, so their address and state must be on the invoice. Add a billing address to the customer; documents issued after that will carry it.`,
      fix: { label: "Open customers", to: "/customers" },
    });
  }

  // --- Lines.
  const invoiceRate = invoice.tax_rate_pct;
  const docLines = invoice.lines.map((l, i) => toLine(l, i, invoiceRate));
  const showHsn = docLines.some((l) => l.hsnSac !== null);
  const missingHsn = docLines.filter((l) => l.hsnSac === null).length;
  if (missingHsn > 0) {
    gaps.push({
      key: "hsn_sac",
      severity: "advisory",
      message:
        missingHsn === docLines.length
          ? "No line on this invoice has an HSN or SAC code, so the column is left off."
          : `${missingHsn} of ${docLines.length} lines have no HSN or SAC code and are left blank.`,
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
      addressLines: supplierAddress,
      pan: livePrint?.pan ?? null,
      bank: livePrint?.bank ?? null,
    },
    shipToLines: lines(invoice.ship_to_address),
    buyer: {
      name: invoice.buyer_name ?? customer.name,
      addressLines: buyerAddress,
      gstin: buyerGstin,
      stateName: stateOf(buyerStateCode),
      stateCode: buyerStateCode,
      contact: customer.contact_name.trim() === "" ? null : customer.contact_name.trim(),
    },
    corrects: original === null ? null : { number: original.number, issuedDate: original.issued_date },
    currency: invoice.currency,
    taxMode: taxModeFor(invoice, sellerStateCode),
    lines: docLines,
    showHsn,
    showDiscount: docLines.some((l) => l.discount > 0),
    summary: summariseTax(docLines),
    totals: {
      taxable: totalsTaxable,
      cgst,
      sgst,
      igst,
      totalTax: mag(invoice.tax_minor),
      roundOff: isCreditNote ? -(invoice.round_off_minor ?? 0) : (invoice.round_off_minor ?? 0),
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
