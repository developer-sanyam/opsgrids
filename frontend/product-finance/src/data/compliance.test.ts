/**
 * The compliance vocabulary is where a wire value becomes something a person acts on, so the
 * things worth pinning are the ones that would be wrong *quietly*: a delta rendered in paise, a
 * terminal failure that reads as "still working on it", and both corrections offered at once when
 * only one of them is legal.
 */

import { describe, expect, it } from "vitest";

import type { EInvoiceState, FilingRemedy, ImsSuggestion, Invoice } from "@/api/types";
import {
  EWAY_STATUS_FILTERS,
  complianceSummary,
  ewayStatusTone,
  filingErrorSentence,
  filingRowLabel,
  filingView,
  periodLabel,
  remedyView,
  suggestionSentence,
} from "./compliance";

function suggestion(over: Partial<ImsSuggestion>): ImsSuggestion {
  return {
    id: "sug-1",
    suggested_action: "pending_action",
    reason: "matched",
    detail: "",
    delta_minor: 0,
    computed_at: "2026-08-21T04:30:00Z",
    ...over,
  };
}

function invoice(over: Partial<Invoice>): Invoice {
  return {
    id: "inv-1",
    number: "INV-2026-0001",
    document_type: "invoice",
    customer_id: "cust-01",
    issued_date: "2026-08-10",
    due_date: "2026-09-09",
    status: "sent",
    currency: "INR",
    lines: [],
    subtotal_minor: 100000,
    tax_rate_pct: 18,
    tax_minor: 18000,
    total_minor: 118000,
    amount_paid_minor: 0,
    notes: null,
    corrects_invoice_id: null,
    ...over,
  };
}

describe("suggestionSentence", () => {
  it("renders an amount mismatch in rupees, not paise, and names the direction", () => {
    expect(
      suggestionSentence(suggestion({ reason: "amount_mismatch", delta_minor: 118000 })),
    ).toBe("₹1,180.00 more than your purchase record.");
    expect(
      suggestionSentence(suggestion({ reason: "amount_mismatch", delta_minor: -177000 })),
    ).toBe("₹1,770.00 less than your purchase record.");
  });

  it("says nothing in the service's vocabulary", () => {
    const wire = ["matched", "supplier_unknown", "no_purchase_record", "duplicate"] as const;
    for (const reason of wire) {
      const sentence = suggestionSentence(suggestion({ reason }));
      expect(sentence).not.toContain("_");
      expect(sentence.endsWith(".")).toBe(true);
    }
  });
});

describe("filingView", () => {
  it("leaves an unreported position blank rather than inventing an obligation", () => {
    expect(filingView(undefined)).toBeNull();
  });

  it("tells a document that needs no IRN apart from one that is merely unfiled", () => {
    expect(filingView({ required: false, status: null, attempts: 0 })?.key).toBe("not_required");
    expect(filingView({ required: true, status: null, attempts: 0 })?.key).toBe("not_filed");
  });

  it("marks a terminal failure as needing a person, and nothing else", () => {
    const failed = filingView({
      required: true,
      status: "failed",
      attempts: 5,
      terminal: true,
      error_code: "retry_exhausted",
    });
    expect(failed?.needsPerson).toBe(true);
    expect(failed?.label).toBe("Filing failed");
    expect(failed?.detail).toContain("retry budget");

    for (const status of ["pending", "filed", "cancelled"] as const) {
      expect(filingView({ required: true, status, attempts: 1 })?.needsPerson).toBe(false);
    }
  });

  it("never describes a document in flight as waiting on a person", () => {
    const inFlight = filingView({ required: true, status: "pending", attempts: 3 });
    expect(inFlight?.detail).toContain("retries on its own");
  });
});

describe("filingErrorSentence", () => {
  it("spells out the platform's own codes and keeps the portal's number", () => {
    expect(filingErrorSentence({ required: true, status: "failed", attempts: 5, error_code: "no_registration" })).toContain(
      "no GST registration",
    );
    expect(
      filingErrorSentence({
        required: true,
        status: "failed",
        attempts: 1,
        error_code: "2189",
        error: "HSN code is not valid",
      }),
    ).toBe("HSN code is not valid (portal code 2189)");
  });

  it("is null when there is no error to report", () => {
    expect(filingErrorSentence({ required: true, status: "filed", attempts: 1 })).toBeNull();
  });
});

describe("remedyView", () => {
  const filed = (remedy: FilingRemedy | null): EInvoiceState => ({
    required: true,
    status: "filed",
    attempts: 1,
    remedy,
  });

  it("offers exactly one correction, never both", () => {
    expect(remedyView(filed("cancel"))?.key).toBe("cancel");
    expect(remedyView(filed("credit_note"))?.key).toBe("credit_note");
  });

  it("offers none when no registration stands", () => {
    expect(remedyView(filed(null))).toBeNull();
    expect(remedyView({ required: true, status: null, attempts: 0 })).toBeNull();
    expect(remedyView(undefined)).toBeNull();
  });

  it("takes the answer from the service rather than re-deciding it from a clock", () => {
    // A `filed` document whose window has closed must still read as credit-note-only, even though
    // nothing here knows what time it is.
    expect(remedyView(filed("credit_note"))?.headline).toContain("closed");
  });
});

describe("complianceSummary", () => {
  const state = (over: Partial<EInvoiceState>): EInvoiceState => ({
    required: true,
    status: null,
    attempts: 0,
    ...over,
  });

  const book: Invoice[] = [
    invoice({ id: "a", issued_date: "2026-08-02", einvoice: state({ status: "filed" }) }),
    invoice({ id: "b", issued_date: "2026-08-05", einvoice: state({ status: "filed" }) }),
    invoice({ id: "c", issued_date: "2026-08-08", einvoice: state({ status: "pending", attempts: 2 }) }),
    invoice({
      id: "d",
      issued_date: "2026-08-11",
      einvoice: state({ status: "failed", attempts: 5, terminal: true }),
    }),
    // B2C: an obligation that does not exist is not an obligation unmet.
    invoice({ id: "e", issued_date: "2026-08-14", einvoice: state({ required: false }) }),
    // A draft is not reportable until it is issued.
    invoice({ id: "f", issued_date: "2026-08-16", status: "draft", einvoice: state({}) }),
    // Last period, and stuck — counted as needing a person, not as this period's arithmetic.
    invoice({
      id: "g",
      issued_date: "2026-07-20",
      einvoice: state({ status: "failed", attempts: 5, terminal: true }),
    }),
    // The service said nothing about this one.
    invoice({ id: "h", issued_date: "2026-08-18" }),
  ];

  const summary = complianceSummary(book, "2026-08");

  it("counts only this period's real obligations", () => {
    expect(summary.required).toBe(4); // a, b, c, d — not the B2C one, the draft or the unreported
    expect(summary.filed).toBe(2);
    expect(summary.awaiting).toBe(1);
  });

  it("carries every stuck document, whatever period it sits in", () => {
    expect(summary.terminal.map((row) => row.id)).toEqual(["d", "g"]);
  });

  it("counts what the service did not report separately from what is unfiled", () => {
    expect(summary.unreported).toBe(1);
  });

  it("names the period in words", () => {
    expect(summary.periodLabel).toBe("August 2026");
    expect(periodLabel("2026-01")).toBe("January 2026");
  });
});

describe("the D3 claim states", () => {
  it("reads a withdrawal in flight as work in progress, not a failure or a submission", () => {
    const view = filingView({ required: true, status: "cancelling", attempts: 1 });
    expect(view?.key).toBe("in_flight");
    expect(view?.label).toBe("Cancelling IRN");
    expect(view?.needsPerson).toBe(false);
    expect(filingRowLabel({ status: "cancelling", ack_date: "2026-08-12T09:00:00Z" })).toBe(
      "Withdrawal requested",
    );
  });

  it("never calls a failed withdrawal a failed filing: the IRN still stands", () => {
    const view = filingView({
      required: true,
      status: "failed",
      ack_date: "2026-08-12T09:00:00Z",
      error_code: "window_closed",
      error: "the 24-hour window closed",
      attempts: 1,
      terminal: false,
      remedy: "contact_support",
    });
    expect(view?.label).toBe("Withdrawal failed");
    expect(view?.detail).toContain("The IRN still stands.");
    expect(filingRowLabel({ status: "failed", ack_date: "2026-08-12T09:00:00Z" })).toBe(
      "Withdrawal refused",
    );
    expect(filingRowLabel({ status: "failed", ack_date: null })).toBe("Refused");
  });

  it("offers a look at the portal, not a button, when the withdrawal could not be confirmed", () => {
    const remedy = remedyView({
      required: true,
      status: "failed",
      attempts: 1,
      remedy: "contact_support",
    });
    expect(remedy?.key).toBe("contact_support");
    expect(remedy?.action).toBe("");
    expect(remedy?.consequence).toContain("credit note");
  });

  it("gives an e-Way bill being raised or withdrawn an in-progress tone and a filter", () => {
    expect(ewayStatusTone("submitting")).toBe(ewayStatusTone("cancelling"));
    expect(ewayStatusTone("submitting")).not.toBe(ewayStatusTone("failed"));
    expect(ewayStatusTone("submitting")).not.toBe(ewayStatusTone("generated"));
    const filters = EWAY_STATUS_FILTERS.map((f) => f.value);
    expect(filters).toContain("submitting");
    expect(filters).toContain("cancelling");
  });
});
