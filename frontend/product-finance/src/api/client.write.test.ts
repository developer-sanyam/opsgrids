/**
 * The write functions against a stubbed `fetch` — method, exact URL and JSON body — the same
 * shape `frontend/opsgrids-client/src/http.test.ts` uses. `USE_FIXTURES` is `false` under
 * `vitest` (no `VITE_USE_FIXTURES` env var), so every one of these takes the live branch.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  archiveCustomer,
  createCustomer,
  fileReturn,
  generateReturn,
  issueInvoice,
  recordPayment,
  recordPurchase,
  setGstRegistration,
  updateCustomer,
} from "./client";

function respond(status: number, body: unknown) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createCustomer", () => {
  it("POSTs to /finance/customers with exactly the given body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(201, { id: "cust-1", name: "Acme" }));
    vi.stubGlobal("fetch", fetchMock);

    await createCustomer({ name: "Acme", gstin: null });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/finance/customers");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ name: "Acme", gstin: null });
  });
});

describe("updateCustomer", () => {
  it("PATCHes to /finance/customers/{id} with only the changed fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, { id: "cust-1", name: "Acme 2" }));
    vi.stubGlobal("fetch", fetchMock);

    await updateCustomer("cust-1", { name: "Acme 2" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/finance/customers/cust-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ name: "Acme 2" });
  });
});

describe("archiveCustomer", () => {
  it("DELETEs /finance/customers/{id} and sends no body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(204, undefined));
    vi.stubGlobal("fetch", fetchMock);

    await archiveCustomer("cust-1");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/finance/customers/cust-1");
    expect(init.method).toBe("DELETE");
    expect(init.body).toBeUndefined();
  });

  it("surfaces the server's 409 reason when the customer is still referenced", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      respond(409, { title: "Conflict", status: 409, detail: "This customer is named on an invoice." }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(archiveCustomer("cust-1")).rejects.toMatchObject({
      status: 409,
      message: "This customer is named on an invoice.",
    });
  });
});

describe("issueInvoice", () => {
  it("POSTs to /finance/invoices with no client-computed totals", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(201, { id: "inv-1", number: "INV-2026-00001" }));
    vi.stubGlobal("fetch", fetchMock);

    const input = {
      customer_id: "cust-1",
      issued_date: "2026-08-24",
      due_date: "2026-09-23",
      tax_rate_pct: 18,
      lines: [{ description: "Widgets", quantity: 10, unit_price_minor: 10000 }],
      document_type: "invoice" as const,
      currency: "INR",
      notes: null,
      corrects_invoice_id: null,
      supply_type: null,
      place_of_supply: null,
      reverse_charge: false,
    };
    await issueInvoice(input);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/finance/invoices");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body).toEqual(input);
    expect(body).not.toHaveProperty("number");
    expect(body).not.toHaveProperty("status");
    expect(body).not.toHaveProperty("total_minor");
  });
});

describe("recordPayment", () => {
  it("POSTs to /finance/payments with the allocations and no computed amount", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(201, { id: "pmt-1" }));
    vi.stubGlobal("fetch", fetchMock);

    const input = {
      reference: "UTR12345",
      customer_id: "cust-1",
      received_date: "2026-08-24",
      method: "bank_transfer" as const,
      allocations: [{ invoice_id: "inv-1", amount_minor: 50000 }],
      note: null,
    };
    await recordPayment(input);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/finance/payments");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body).toEqual(input);
    expect(body).not.toHaveProperty("amount_minor");
  });
});

describe("setGstRegistration", () => {
  it("PUTs to /finance/gst/registration", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, { id: "reg-1", gstin: "27AABCA9012D1Z5" }));
    vi.stubGlobal("fetch", fetchMock);

    await setGstRegistration({ gstin: "27AABCA9012D1Z5", legal_name: "Acme Pvt Ltd" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/finance/gst/registration");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ gstin: "27AABCA9012D1Z5", legal_name: "Acme Pvt Ltd" });
  });
});

describe("recordPurchase", () => {
  it("POSTs to /finance/purchases with no tax-head inputs", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(201, { id: "pur-1" }));
    vi.stubGlobal("fetch", fetchMock);

    const input = {
      supplier_gstin: "27AACCS4413K1ZP",
      number: "SUP-001",
      document_date: "2026-08-24",
      taxable_minor: 100000,
      tax_rate_pct: 18,
    };
    await recordPurchase(input);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/finance/purchases");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body).toEqual(input);
    expect(body).not.toHaveProperty("cgst_minor");
  });
});

describe("generateReturn / fileReturn", () => {
  it("POSTs to /finance/gst/returns", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(201, { id: "ret-1", period: "2026-08" }));
    vi.stubGlobal("fetch", fetchMock);

    await generateReturn({ period: "2026-08", return_type: "gstr1" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/finance/gst/returns");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ period: "2026-08", return_type: "gstr1" });
  });

  it("POSTs to /finance/gst/returns/{id}/filing with the ARN", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(201, { id: "ret-1", status: "filed" }));
    vi.stubGlobal("fetch", fetchMock);

    await fileReturn("ret-1", { arn: "AA270826001234A" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/finance/gst/returns/ret-1/filing");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ arn: "AA270826001234A" });
  });
});
