import { describe, expect, it } from "vitest";

import { FILINGS_BY_INVOICE, GST_REGISTRATION, INVOICES } from "@/api/fixtures";
import type { Customer, Invoice, InvoiceLine } from "@/api/types";
import { CUSTOMERS } from "@/data/customers";

import {
  buildTaxInvoice,
  documentFileTitle,
  isPrintable,
  summariseTax,
  type TaxInvoiceInputs,
  type TaxInvoiceLine,
} from "./taxInvoice";

const customer = (id: string): Customer => {
  const found = CUSTOMERS.find((c) => c.id === id);
  if (!found) throw new Error(`no fixture customer ${id}`);
  return found;
};

function line(over: Partial<InvoiceLine> = {}): InvoiceLine {
  return {
    id: "l1",
    description: "MS plates 10mm",
    quantity: 2,
    unit_price_minor: 100_000,
    amount_minor: 200_000,
    hsn_sac: "7208",
    tax_rate_pct: 18,
    cgst_minor: 18_000,
    sgst_minor: 18_000,
    igst_minor: 0,
    ...over,
  };
}

function invoice(over: Partial<Invoice> = {}): Invoice {
  return {
    id: "inv-1",
    number: "INV-2026-0482",
    document_type: "invoice",
    customer_id: "cust-03",
    issued_date: "2026-08-01",
    due_date: "2026-08-31",
    status: "sent",
    currency: "INR",
    lines: [line()],
    subtotal_minor: 200_000,
    tax_rate_pct: 18,
    tax_minor: 36_000,
    total_minor: 236_000,
    amount_paid_minor: 0,
    notes: null,
    corrects_invoice_id: null,
    place_of_supply: "27",
    supply_type: "b2b",
    reverse_charge: false,
    seller_gstin: GST_REGISTRATION.gstin,
    buyer_gstin: "27AAECO9012H1ZG",
    cgst_minor: 18_000,
    sgst_minor: 18_000,
    igst_minor: 0,
    einvoice: { required: false, status: null, attempts: 0 },
    ...over,
  };
}

function inputs(over: Partial<TaxInvoiceInputs> = {}): TaxInvoiceInputs {
  return {
    invoice: invoice(),
    customer: customer("cust-03"),
    registration: GST_REGISTRATION,
    original: null,
    filings: null,
    portalConnected: true,
    ...over,
  };
}

const taxLine = (over: Partial<TaxInvoiceLine>): TaxInvoiceLine => ({
  number: 1,
  description: "x",
  hsnSac: "7208",
  quantity: 1,
  unit: null,
  ratePerUnit: 0,
  discount: 0,
  taxable: 0,
  taxRatePct: 18,
  cgst: 0,
  sgst: 0,
  igst: 0,
  lineTotal: 0,
  ...over,
});

describe("summariseTax", () => {
  it("adds together lines that share an HSN/SAC and a rate", () => {
    const rows = summariseTax([
      taxLine({ taxable: 100_000, cgst: 9_000, sgst: 9_000 }),
      taxLine({ number: 2, taxable: 50_000, cgst: 4_500, sgst: 4_500 }),
    ]);
    expect(rows).toEqual([
      { hsnSac: "7208", taxRatePct: 18, taxable: 150_000, cgst: 13_500, sgst: 13_500, igst: 0, totalTax: 27_000 },
    ]);
  });

  it("keeps different rates under the same code apart, ordered by code then rate", () => {
    const rows = summariseTax([
      taxLine({ hsnSac: "9954", taxRatePct: 18, taxable: 10_000, igst: 1_800 }),
      taxLine({ number: 2, hsnSac: "7208", taxRatePct: 28, taxable: 10_000, igst: 2_800 }),
      taxLine({ number: 3, hsnSac: "7208", taxRatePct: 5, taxable: 10_000, igst: 500 }),
    ]);
    expect(rows.map((r) => [r.hsnSac, r.taxRatePct])).toEqual([
      ["7208", 5],
      ["7208", 28],
      ["9954", 18],
    ]);
  });

  it("groups lines with no code together instead of dropping them, so it still foots", () => {
    const rows = summariseTax([
      taxLine({ hsnSac: null, taxable: 10_000, cgst: 900, sgst: 900 }),
      taxLine({ number: 2, hsnSac: null, taxable: 20_000, cgst: 1_800, sgst: 1_800 }),
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0]?.taxable).toBe(30_000);
    expect(rows[0]?.totalTax).toBe(5_400);
  });
});

describe("buildTaxInvoice: a complete invoice", () => {
  it("is printable, titled a tax invoice, and uses CGST and SGST within a state", () => {
    const { model, gaps } = buildTaxInvoice(inputs());
    expect(gaps).toEqual([]);
    expect(isPrintable(gaps)).toBe(true);
    expect(model.title).toBe("Tax Invoice");
    expect(model.taxMode).toBe("intra");
    expect(model.supplier.displayName).toBe("OpsGrids Demo");
    expect(model.supplier.legalName).toBe("OpsGrids Demo Trading Pvt Ltd");
    expect(model.supplier.stateName).toBe("Maharashtra");
    expect(model.placeOfSupply).toEqual({ name: "Maharashtra", code: "27" });
    expect(model.totals.total).toBe(236_000);
    expect(model.amountInWordsMinor).toBe(236_000);
  });

  it("charges IGST across states", () => {
    const { model } = buildTaxInvoice(
      inputs({
        invoice: invoice({
          place_of_supply: "29",
          buyer_gstin: "29AADCS3456J1Z0",
          cgst_minor: 0,
          sgst_minor: 0,
          igst_minor: 36_000,
          lines: [line({ cgst_minor: 0, sgst_minor: 0, igst_minor: 36_000 })],
        }),
        customer: customer("cust-04"),
      }),
    );
    expect(model.taxMode).toBe("inter");
    expect(model.totals.igst).toBe(36_000);
    expect(model.summary[0]?.igst).toBe(36_000);
  });

  it("falls back to the two states when no tax was charged", () => {
    const { model } = buildTaxInvoice(
      inputs({
        invoice: invoice({
          place_of_supply: "29",
          cgst_minor: 0,
          sgst_minor: 0,
          igst_minor: 0,
          tax_minor: 0,
          total_minor: 200_000,
          reverse_charge: true,
          lines: [line({ cgst_minor: 0, sgst_minor: 0, igst_minor: 0 })],
        }),
      }),
    );
    expect(model.taxMode).toBe("inter");
    expect(model.reverseCharge).toBe(true);
  });
});

describe("buildTaxInvoice: missing supplier facts", () => {
  it("blocks the document and points at settings when no registration exists", () => {
    const { model, gaps } = buildTaxInvoice(inputs({ registration: null }));
    expect(isPrintable(gaps)).toBe(false);
    const gap = gaps.find((g) => g.key === "supplier_registration");
    expect(gap?.severity).toBe("blocking");
    expect(gap?.fix?.to).toBe("/settings");
    // The GSTIN is still the one the invoice was raised under; the name is simply absent.
    expect(model.supplier.gstin).toBe(GST_REGISTRATION.gstin);
    expect(model.supplier.displayName).toBeNull();
  });

  it("blocks when neither the invoice nor a registration has a GSTIN", () => {
    const { model, gaps } = buildTaxInvoice(
      inputs({ invoice: invoice({ seller_gstin: null }), registration: null }),
    );
    expect(gaps.map((g) => g.key)).toContain("supplier_gstin");
    expect(model.supplier.gstin).toBeNull();
    expect(isPrintable(gaps)).toBe(false);
  });

  it("refuses to borrow the name of a different registration", () => {
    const { model, gaps } = buildTaxInvoice(
      inputs({ invoice: invoice({ seller_gstin: "29AABCA9012D1Z1" }) }),
    );
    expect(gaps.map((g) => g.key)).toContain("supplier_mismatch");
    expect(model.supplier.displayName).toBeNull();
    expect(model.supplier.gstin).toBe("29AABCA9012D1Z1");
    expect(model.supplier.stateName).toBe("Karnataka");
  });

  it("blocks when the place of supply was never recorded", () => {
    const { model, gaps } = buildTaxInvoice(inputs({ invoice: invoice({ place_of_supply: null }) }));
    expect(gaps.map((g) => g.key)).toContain("place_of_supply");
    expect(model.placeOfSupply).toBeNull();
  });

  it("warns, without blocking, on a B2B buyer with no GSTIN and on lines with no code", () => {
    const { model, gaps } = buildTaxInvoice(
      inputs({
        invoice: invoice({ buyer_gstin: null, lines: [line({ hsn_sac: null })] }),
      }),
    );
    expect(gaps.map((g) => [g.key, g.severity])).toEqual([
      ["buyer_gstin", "advisory"],
      ["hsn_sac", "advisory"],
    ]);
    expect(isPrintable(gaps)).toBe(true);
    expect(model.showHsn).toBe(false);
    expect(model.buyer.gstin).toBeNull();
  });
});

describe("buildTaxInvoice: credit notes", () => {
  const creditNote = invoice({
    number: "CN-2026-0008",
    document_type: "credit_note",
    corrects_invoice_id: "inv-orig",
    subtotal_minor: -200_000,
    tax_minor: -36_000,
    total_minor: -236_000,
    cgst_minor: -18_000,
    sgst_minor: -18_000,
    lines: [
      line({
        quantity: -2,
        amount_minor: -200_000,
        cgst_minor: -18_000,
        sgst_minor: -18_000,
      }),
    ],
  });
  const original = invoice({ id: "inv-orig", number: "INV-2026-0155", issued_date: "2026-07-01" });

  it("renders as a Credit Note naming the invoice it corrects, with magnitudes", () => {
    const { model, gaps } = buildTaxInvoice(inputs({ invoice: creditNote, original }));
    expect(gaps).toEqual([]);
    expect(model.title).toBe("Credit Note");
    expect(model.kind).toBe("credit_note");
    expect(model.corrects).toEqual({ number: "INV-2026-0155", issuedDate: "2026-07-01" });
    expect(model.totals.total).toBe(236_000);
    expect(model.amountInWordsMinor).toBe(236_000);
    expect(model.lines[0]?.quantity).toBe(2);
    expect(model.lines[0]?.taxable).toBe(200_000);
  });

  it("blocks when the original invoice could not be loaded", () => {
    const { model, gaps } = buildTaxInvoice(inputs({ invoice: creditNote, original: null }));
    expect(gaps.map((g) => g.key)).toContain("original_invoice");
    expect(isPrintable(gaps)).toBe(false);
    expect(model.corrects).toBeNull();
  });
});

describe("buildTaxInvoice: e-invoice", () => {
  const filed = INVOICES.find((i) => i.einvoice?.status === "filed" && i.einvoice.irn);
  const filings = filed ? (FILINGS_BY_INVOICE.get(filed.id) ?? null) : null;

  it("carries the IRN, acknowledgement and signed QR once registered", () => {
    expect(filed).toBeDefined();
    if (!filed) return;
    const { model, gaps } = buildTaxInvoice(
      inputs({ invoice: filed, customer: customer(filed.customer_id), filings }),
    );
    expect(model.einvoice?.irn).toBe(filed.einvoice?.irn);
    expect(model.einvoice?.qrPayload).toBeTruthy();
    expect(gaps.map((g) => g.key)).not.toContain("qr_missing");
  });

  it("blocks when the IRN is on file but the QR cannot be loaded", () => {
    if (!filed) throw new Error("fixture has no filed invoice");
    const { gaps } = buildTaxInvoice(
      inputs({ invoice: filed, customer: customer(filed.customer_id), filings: null }),
    );
    expect(gaps.find((g) => g.key === "qr_missing")?.severity).toBe("blocking");
  });

  it("prints no IRN or QR while the portal is a simulator, and says why", () => {
    if (!filed) throw new Error("fixture has no filed invoice");
    const { model, gaps } = buildTaxInvoice(
      inputs({ invoice: filed, customer: customer(filed.customer_id), filings, portalConnected: false }),
    );
    expect(model.einvoice).toBeNull();
    expect(gaps.map((g) => g.key)).toContain("portal_simulated");
    expect(isPrintable(gaps)).toBe(true);
  });

  it("warns when an IRN is required and absent", () => {
    const { gaps } = buildTaxInvoice(
      inputs({ invoice: invoice({ einvoice: { required: true, status: null, attempts: 0 } }) }),
    );
    expect(gaps.map((g) => g.key)).toContain("irn_missing");
  });
});

describe("buildTaxInvoice — Rule 46 supplier, buyer and line facts", () => {
  it("prints what the document froze at issue over the live records", () => {
    const { model, gaps } = buildTaxInvoice(
      inputs({
        registration: null,
        invoice: invoice({
          seller_legal_name: "Frozen Industries Pvt Ltd",
          seller_address: "Plot 1\nPune 411001",
          buyer_name: "Frozen Buyer",
          buyer_address: "12 FC Road\nPune 411004",
          buyer_state_code: "27",
          ship_to_address: "Godown 3\nChakan",
        }),
      }),
    );
    expect(model.supplier.displayName).toBe("Frozen Industries Pvt Ltd");
    expect(model.supplier.addressLines).toEqual(["Plot 1", "Pune 411001"]);
    expect(model.buyer.name).toBe("Frozen Buyer");
    expect(model.buyer.addressLines).toEqual(["12 FC Road", "Pune 411004"]);
    expect(model.shipToLines).toEqual(["Godown 3", "Chakan"]);
    expect(gaps.map((g) => g.key)).not.toContain("supplier_registration");
  });

  it("blocks printing a document issued with snapshots when the registration has no address", () => {
    const { gaps } = buildTaxInvoice(
      inputs({
        invoice: invoice({ seller_legal_name: GST_REGISTRATION.legal_name, seller_address: null }),
        registration: { ...GST_REGISTRATION, address_line1: "", address_line2: "", city: "", pincode: "" },
      }),
    );
    expect(gaps.find((g) => g.key === "supplier_address")?.severity).toBe("blocking");
    expect(isPrintable(gaps)).toBe(false);
  });

  it("reprints a document issued before snapshots existed exactly as it was, without today's address, PAN or bank", () => {
    // Every invoice already in production has no seller snapshot. Its reprint must not pick up
    // supplier details that were added to the settings after it was issued.
    const legacy = invoice();
    const { model, gaps } = buildTaxInvoice(
      inputs({
        invoice: legacy,
        registration: GST_REGISTRATION,
        printDetails: {
          gstin: GST_REGISTRATION.gstin,
          legal_name: GST_REGISTRATION.legal_name,
          trade_name: null,
          state_code: GST_REGISTRATION.state_code,
          address_line1: "",
          address_line2: "",
          city: "",
          pincode: "",
          pan: null,
          bank: null,
        },
      }),
    );
    expect(gaps.map((g) => g.key)).not.toContain("supplier_address");
    expect(isPrintable(gaps)).toBe(true);
    expect(model.supplier.addressLines).toEqual([]);
    expect(model.supplier.pan).toBeNull();
    expect(model.supplier.bank).toBeNull();
    expect(model.buyer.name).toBe(customer("cust-03").name);
    expect(model.totals.roundOff).toBe(0);
  });

  it("uses live bank details only for the registration the invoice names", () => {
    const details = {
      gstin: GST_REGISTRATION.gstin,
      legal_name: GST_REGISTRATION.legal_name,
      trade_name: null,
      state_code: GST_REGISTRATION.state_code,
      address_line1: "Plot 14",
      address_line2: "",
      city: "Pune",
      pincode: "411026",
      pan: "AABCA9012D",
      bank: { account_name: "A", account_number: "50100123456789", ifsc: "HDFC0001234", bank_name: "HDFC", branch: "", upi_id: "" },
    };
    const same = buildTaxInvoice(inputs({ printDetails: details }));
    expect(same.model.supplier.bank?.account_number).toBe("50100123456789");
    expect(same.model.supplier.pan).toBe("AABCA9012D");
    const other = buildTaxInvoice(inputs({ printDetails: { ...details, gstin: "29AABCA9012D1ZX" } }));
    expect(other.model.supplier.bank).toBeNull();
  });

  it("asks for an unregistered buyer's address from ₹50,000", () => {
    const b2c = invoice({ buyer_gstin: null, supply_type: "b2c", subtotal_minor: 50_000_00 });
    const noAddress = { ...customer("cust-03"), gstin: null, address_line1: "", address_line2: "", pincode: "", city: "" };
    const { gaps } = buildTaxInvoice(inputs({ invoice: b2c, customer: noAddress }));
    expect(gaps.find((g) => g.key === "buyer_address")?.severity).toBe("advisory");
  });

  it("carries units, discounts and the round-off", () => {
    const { model } = buildTaxInvoice(
      inputs({
        invoice: invoice({
          lines: [line({ unit: "NOS", discount_minor: 5_000 })],
          round_off_minor: 29,
        }),
      }),
    );
    expect(model.lines[0]?.unit).toBe("NOS");
    expect(model.lines[0]?.discount).toBe(5_000);
    expect(model.showDiscount).toBe(true);
    expect(model.totals.roundOff).toBe(29);
  });
});

describe("documentFileTitle", () => {
  it("joins the number and the customer, and strips characters a file name cannot hold", () => {
    expect(documentFileTitle("INV-2026-0482", "Orbit Logistics")).toBe("INV-2026-0482 - Orbit Logistics");
    expect(documentFileTitle("INV/2026/1", 'A: "B"  Ltd')).toBe("INV-2026-1 - A- -B- Ltd");
  });
});
