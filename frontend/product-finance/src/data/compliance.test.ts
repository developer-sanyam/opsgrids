/**
 * The compliance vocabulary is where a wire value becomes something a person acts on, so the
 * things worth pinning are the ones that would be wrong *quietly*: a delta rendered in paise, a
 * terminal failure that reads as "still working on it", and both corrections offered at once when
 * only one of them is legal.
 */

import { describe, expect, it } from "vitest";

import type {
  EInvoiceState,
  EwayState,
  FilingRemedy,
  ImsPush,
  ImsSuggestion,
  Invoice,
} from "@/api/types";
import {
  EWAY_STATUS_FILTERS,
  PUSH_STATUS_FILTERS,
  actionLabel,
  bulkConsequence,
  complianceSummary,
  driftSentence,
  needsReason,
  portalActionLabel,
  pushView,
  ewayStateView,
  ewayStatusTone,
  filingErrorSentence,
  filingRowLabel,
  filingView,
  formatCountdown,
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

describe("the e-Way retry countdown", () => {
  const at = Date.parse("2026-09-24T10:00:00Z");

  it("reads as minutes and seconds, then hours and minutes, then seconds", () => {
    expect(formatCountdown("2026-09-24T10:04:05Z", at)).toEqual({ label: "4m 05s", due: false });
    expect(formatCountdown("2026-09-24T11:02:00Z", at)).toEqual({ label: "1h 02m", due: false });
    expect(formatCountdown("2026-09-24T10:00:12Z", at)).toEqual({ label: "12s", due: false });
  });

  it("rounds a partial second up so it never reads 0s while still counting", () => {
    expect(formatCountdown("2026-09-24T10:00:00.400Z", at)).toEqual({ label: "1s", due: false });
  });

  it("says the retry is due once the moment has passed, never a negative", () => {
    expect(formatCountdown("2026-09-24T10:00:00Z", at)).toEqual({ label: "any moment now", due: true });
    expect(formatCountdown("2026-09-24T09:00:00Z", at)).toEqual({ label: "any moment now", due: true });
  });

  it("does not invent a countdown from a value it cannot read", () => {
    expect(formatCountdown("not a date", at).due).toBe(true);
  });
});

describe("what an e-Way position says", () => {
  function state(over: Partial<EwayState>): EwayState {
    return {
      required: true,
      status: null,
      eway_bill_no: null,
      valid_from: null,
      valid_until: null,
      terminal: false,
      cancellable_until: null,
      remedy: null,
      attempts: 0,
      max_attempts: 8,
      ...over,
    };
  }

  it("says which attempt is running, and that the platform is still working on it", () => {
    const view = ewayStateView(state({ status: "pending", attempt: 3, error_code: "unavailable" }));
    expect(view?.headline).toBe("Retrying — attempt 3 of 8");
    expect(view?.active).toBe(true);
    expect(view?.needsPerson).toBe(false);
    expect(ewayStateView(state({ status: "submitting", attempt: 1 }))?.headline).toBe(
      "Being raised — attempt 1 of 8",
    );
  });

  it("does not claim an attempt number it was not given", () => {
    expect(ewayStateView(state({ status: "pending" }))?.headline).toBe("Retrying");
  });

  it("tells a refusal from a spent budget, because they call for opposite actions", () => {
    const refused = ewayStateView(
      state({ status: "failed", terminal: true, error_code: "113", remedy_detail: "Add the vehicle." }),
    );
    const spent = ewayStateView(
      state({ status: "failed", terminal: true, error_code: "retry_exhausted" }),
    );
    expect(refused?.headline).toBe("Refused by the portal (code 113)");
    expect(refused?.detail).toBe("Add the vehicle.");
    expect(spent?.headline).toBe("Gave up — the portal never answered");
    expect(refused?.needsPerson).toBe(true);
    expect(spent?.needsPerson).toBe(true);
    expect(refused?.active).toBe(false);
  });

  it("names the other platform failures", () => {
    expect(ewayStateView(state({ status: "failed", terminal: true, error_code: "stale" }))?.headline).toBe(
      "Not raised — the request went stale",
    );
    expect(
      ewayStateView(state({ status: "failed", terminal: true, error_code: "no_registration" }))?.headline,
    ).toBe("Not raised — no GST registration");
  });

  it("stays silent about a bill that stands and about a failed withdrawal", () => {
    expect(ewayStateView(state({ status: "generated" }))).toBeNull();
    expect(ewayStateView(state({ status: "cancelled", terminal: true }))).toBeNull();
    // A failed withdrawal is not terminal: the bill stands, so it is not a generation failure.
    expect(ewayStateView(state({ status: "failed", terminal: false }))).toBeNull();
  });
});

function push(over: Partial<ImsPush>): ImsPush {
  return {
    id: "push-1",
    decision_id: "dec-1",
    ims_document_id: "doc-1",
    period: "2026-08",
    action: "accept",
    version: 1,
    status: "queued",
    is_current: true,
    requested_at: null,
    attempts: 0,
    max_attempts: 6,
    next_attempt_at: null,
    pushed_at: null,
    error_code: null,
    error: null,
    portal: null,
    observed_action: null,
    observed_at: null,
    drift: false,
    drift_detected_at: null,
    ...over,
  };
}

describe("where a decision's push stands", () => {
  it("says nothing for a document nobody decided", () => {
    expect(pushView(null)).toBeNull();
    expect(pushView(undefined)).toBeNull();
  });

  it("tells waiting-for-a-person from waiting-for-the-worker", () => {
    const unasked = pushView(push({}));
    const asked = pushView(push({ requested_at: "2026-09-24T06:00:00Z" }));
    expect(unasked?.label).toBe("Queued to send");
    expect(unasked?.active).toBe(false);
    expect(unasked?.detail).toMatch(/GSTN has not been told/);
    expect(asked?.label).toBe("Waiting to send");
    expect(asked?.active).toBe(true);
  });

  it("calls an outage that is being retried a retry, with the attempt it is on", () => {
    const view = pushView(
      push({ requested_at: "2026-09-24T06:00:00Z", attempts: 2, next_attempt_at: "2026-09-24T06:04:00Z" }),
    );
    expect(view?.label).toBe("Retrying");
    expect(view?.detail).toMatch(/Attempt 2 of 6/);
    expect(view?.needsPerson).toBe(false);
  });

  it("puts a refusal in front of a person, with the portal's code and reason", () => {
    const view = pushView(push({ status: "failed", error_code: "IMS201", error: "document not found on the portal" }));
    expect(view?.label).toBe("Send failed");
    expect(view?.detail).toBe("Code IMS201 — document not found on the portal");
    expect(view?.needsPerson).toBe(true);
  });

  it("says when the platform gave up on an outage, not that the portal refused", () => {
    expect(pushView(push({ status: "failed", error_code: "retry_exhausted" }))?.label).toBe(
      "Send failed — gave up",
    );
  });

  it("names what GSTN holds on a conflict and says the way out is a new decision", () => {
    const view = pushView(push({ status: "conflict", observed_action: "rejected" }));
    expect(view?.label).toBe("Conflict on GSTN");
    expect(view?.detail).toMatch(/holds rejected/);
    expect(view?.detail).toMatch(/Decide it again/);
    expect(view?.needsPerson).toBe(true);
  });

  it("does not claim a pushed decision is settled when the portal has since disagreed", () => {
    const settled = pushView(push({ status: "pushed" }));
    const drifted = pushView(push({ status: "pushed", drift: true, observed_action: "pending" }));
    expect(settled?.detail).toBe("GSTN holds this decision.");
    expect(drifted?.detail).toBeNull();
  });

  it("puts the words of drift in the portal's terms, not the platform's", () => {
    const sentence = driftSentence(push({ status: "pushed", action: "accept", drift: true, observed_action: "pending" }));
    expect(sentence).toBe("You sent accepted; the last sync found GSTN showing on hold.");
    expect(portalActionLabel(null)).toBe("unknown");
    expect(portalActionLabel("something_new")).toBe("something_new");
  });
});

describe("what a decision is asked to say", () => {
  it("needs a reason to reject or hold, and not to accept or to withdraw", () => {
    expect(needsReason("reject")).toBe(true);
    expect(needsReason("pending_action")).toBe(true);
    expect(needsReason("accept")).toBe(false);
    expect(needsReason("no_action")).toBe(false);
  });

  it("states what each bulk action does to the credit before it is confirmed", () => {
    expect(bulkConsequence("accept")).toMatch(/Claims the input tax credit/);
    expect(bulkConsequence("accept")).toMatch(/general business purchase/);
    expect(bulkConsequence("reject")).toMatch(/Declines the credit/);
    expect(bulkConsequence("no_action")).toMatch(/takes back any credit/);
    expect(actionLabel("no_action")).toBe("Take no action");
  });

  it("filters by every push state, and by drift as its own", () => {
    expect(PUSH_STATUS_FILTERS.map((f) => f.value)).toEqual([
      "all",
      "queued",
      "submitting",
      "pushed",
      "failed",
      "conflict",
      "drift",
    ]);
  });
});
