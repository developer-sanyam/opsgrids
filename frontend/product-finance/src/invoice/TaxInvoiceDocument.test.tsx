import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FILINGS_BY_INVOICE, GST_REGISTRATION, INVOICES } from "@/api/fixtures";
import { CUSTOMERS } from "@/data/customers";

import { TaxInvoiceDocument } from "./TaxInvoiceDocument";
import { buildTaxInvoice } from "./taxInvoice";

function modelFor(number: string, over: { registration?: null; connected?: boolean } = {}) {
  const invoice = INVOICES.find((i) => i.number === number);
  const customer = CUSTOMERS.find((c) => c.id === invoice?.customer_id);
  if (!invoice || !customer) throw new Error(`fixture ${number} missing`);
  const original = invoice.corrects_invoice_id
    ? (INVOICES.find((i) => i.id === invoice.corrects_invoice_id) ?? null)
    : null;
  return buildTaxInvoice({
    invoice,
    customer,
    registration: over.registration === null ? null : GST_REGISTRATION,
    original,
    filings: FILINGS_BY_INVOICE.get(invoice.id) ?? null,
    portalConnected: over.connected ?? true,
  }).model;
}

const filedNumber = INVOICES.find((i) => i.einvoice?.status === "filed" && i.einvoice.irn)?.number ?? "";

describe("TaxInvoiceDocument", () => {
  it("is a tax invoice marked original for recipient, led by the organization's name", () => {
    render(<TaxInvoiceDocument model={modelFor(filedNumber)} />);
    expect(screen.getByRole("heading", { level: 1, name: "Tax Invoice" })).toBeTruthy();
    expect(screen.getByText("Original for recipient")).toBeTruthy();
    expect(screen.getByText("OpsGrids Demo")).toBeTruthy();
    expect(screen.getAllByText(GST_REGISTRATION.gstin).length).toBeGreaterThan(0);
  });

  it("states the total in figures and in Indian-system words", () => {
    const model = modelFor(filedNumber);
    render(<TaxInvoiceDocument model={model} />);
    expect(screen.getByText(/Amount in words:/)).toBeTruthy();
    expect(screen.getByText(/^Indian Rupees .* Only$/)).toBeTruthy();
  });

  it("prints the IRN, acknowledgement and signed QR for a registered e-invoice", () => {
    const model = modelFor(filedNumber);
    render(<TaxInvoiceDocument model={model} />);
    const section = screen.getByRole("region", { name: "E-invoice" });
    expect(within(section).getByText(model.einvoice?.irn ?? "")).toBeTruthy();
    expect(within(section).getByRole("img", { name: /Signed e-invoice QR code/ })).toBeTruthy();
  });

  it("prints no e-invoice block while the portal is a simulator", () => {
    render(<TaxInvoiceDocument model={modelFor(filedNumber, { connected: false })} />);
    expect(screen.queryByRole("region", { name: "E-invoice" })).toBeNull();
  });

  it("renders a credit note under its own title, referencing the original invoice", () => {
    const model = modelFor("CN-2026-0008");
    render(<TaxInvoiceDocument model={model} />);
    expect(screen.getByRole("heading", { level: 1, name: "Credit Note" })).toBeTruthy();
    expect(screen.getByText(/Against invoice/)).toBeTruthy();
    expect(screen.getByText(/INV-2026-0155 dated/)).toBeTruthy();
    expect(screen.queryByText("Due date")).toBeNull();
    expect(document.body.textContent).not.toMatch(/−|-₹|(^|[^A-Za-z0-9])-\d/);
  });

  it("labels each figure of a line and of the tax summary, so the phone layout can stack them", () => {
    render(<TaxInvoiceDocument model={modelFor(filedNumber)} />);
    const lines = screen.getByRole("table", { name: /^Items on / });
    expect(lines.classList.contains("stack-rows")).toBe(true);
    const row = lines.querySelectorAll("tbody tr")[0];
    const labels = Array.from(row?.querySelectorAll("td[data-label]") ?? []).map((cell) =>
      cell.getAttribute("data-label"),
    );
    expect(labels).toContain("Qty");
    expect(labels).toContain("Amount (₹)");
    expect(row?.querySelector("td.stack-title")).not.toBeNull();
    const summary = screen.getByRole("table", { name: "Tax summary" });
    expect(summary.classList.contains("stack-rows")).toBe(true);
    expect(summary.querySelector("tbody td[data-label='Taxable value']")).not.toBeNull();
  });

  it("leaves a missing supplier name off the page instead of printing a stand-in", () => {
    render(<TaxInvoiceDocument model={modelFor(filedNumber, { registration: null })} />);
    expect(screen.queryByText("OpsGrids Demo")).toBeNull();
    expect(screen.queryByText(/^For /)).toBeNull();
    expect(document.body.textContent).not.toMatch(/undefined|null|N\/A/);
  });
});
