import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { PermissionProvider } from "@opsgrids/client";

import type { EwayBill, EwayBillWithInvoice, EwayState, Page } from "@/api/types";
import { EwayBillsPage } from "./EwayBills";

const { listMock } = vi.hoisted(() => ({ listMock: vi.fn() }));

vi.mock("@/api/client", () => ({
  listEwayBills: listMock,
  retryEwayBill: vi.fn(),
}));

function bill(over: Partial<EwayBill>): EwayBill {
  return {
    id: "b-1",
    invoice_id: "inv-1",
    status: "pending",
    effective_status: "pending",
    eway_bill_no: null,
    transport_mode: "road",
    distance_km: 180,
    vehicle_no: "MH14CD5678",
    transporter_id: null,
    transport_doc_no: null,
    transport_doc_date: null,
    over_dimensional: false,
    consignment_value_minor: 1_180_000_00,
    valid_from: null,
    valid_until: null,
    remedy: null,
    error_code: "unavailable",
    error: "connection reset by peer",
    attempted_at: "2026-09-24T06:00:00Z",
    attempt: 2,
    retry_at: null,
    ...over,
  };
}

function state(over: Partial<EwayState>): EwayState {
  return {
    required: true,
    status: "pending",
    eway_bill_no: null,
    valid_from: null,
    valid_until: null,
    terminal: false,
    cancellable_until: null,
    remedy: null,
    attempts: 2,
    remedy_detail: null,
    error_code: "unavailable",
    error: "connection reset by peer",
    attempt: 2,
    max_attempts: 8,
    retry_at: new Date(Date.now() + 3 * 60_000).toISOString(),
    can_retry: true,
    ...over,
  };
}

function row(over: Partial<EwayBillWithInvoice> & { bill: EwayBill }): EwayBillWithInvoice {
  return { invoice_number: "INV-1", customer_name: "Consignee", is_current: true, state: null, ...over };
}

function page(items: EwayBillWithInvoice[]): Page<EwayBillWithInvoice> {
  return { items, total: items.length, page: 1, size: 25, pages: 1 };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <PermissionProvider grants={["finance.compliance.eway.retry:ORG"]}>
        <EwayBillsPage />
      </PermissionProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  listMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the e-Way register", () => {
  it("tells the retry story on a document's current row only", async () => {
    listMock.mockResolvedValue(
      page([
        row({ bill: bill({ id: "b-2", attempt: 2 }), invoice_number: "INV-1", state: state({}) }),
        // The older outage row of the same document: history, so no countdown and no retry.
        row({ bill: bill({ id: "b-1", attempt: 1 }), invoice_number: "INV-1", is_current: false }),
      ]),
    );
    renderPage();

    expect(await screen.findByText("Retrying — attempt 2 of 8")).toBeTruthy();
    expect(screen.getAllByRole("timer")).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Retry now…" })).toHaveLength(1);
    // The history row still reads as what it was, in words.
    expect(screen.getByText("pending")).toBeTruthy();
  });

  it("shows a refusal with the portal's code and no retry beside it", async () => {
    listMock.mockResolvedValue(
      page([
        row({
          bill: bill({ status: "failed", effective_status: "failed", error_code: "113", error: "vehicle missing" }),
          state: state({
            status: "failed",
            terminal: true,
            error_code: "113",
            remedy: "correct_and_resubmit",
            remedy_detail: "Add the vehicle number (Part B) and raise the bill again.",
            can_retry: false,
            retry_at: null,
          }),
        }),
      ]),
    );
    renderPage();

    expect(await screen.findByText("Refused by the portal (code 113)")).toBeTruthy();
    expect(screen.getByText(/Add the vehicle number/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });

  it("shows a bill that stands as an ordinary status, with no retry machinery", async () => {
    listMock.mockResolvedValue(
      page([
        row({
          bill: bill({
            status: "generated",
            effective_status: "generated",
            eway_bill_no: "351009876543",
            error_code: null,
            error: null,
          }),
          state: null,
        }),
      ]),
    );
    renderPage();

    expect(await screen.findByText("generated")).toBeTruthy();
    expect(screen.queryByRole("timer")).toBeNull();
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });

  it("re-reads quietly while something is being retried, without a skeleton in between", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    listMock
      .mockResolvedValueOnce(page([row({ bill: bill({}), state: state({}) })]))
      .mockResolvedValue(
        page([
          row({
            bill: bill({ status: "generated", effective_status: "generated", eway_bill_no: "351009876543" }),
            state: null,
          }),
        ]),
      );
    const { container } = renderPage();
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText("Retrying — attempt 2 of 8")).toBeTruthy();
    expect(listMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(15_000);
      await Promise.resolve();
    });

    expect(listMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("generated")).toBeTruthy();
    expect(screen.queryByText("Retrying — attempt 2 of 8")).toBeNull();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBe(0);
  });

  it("does not poll while nothing is being retried", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    listMock.mockResolvedValue(
      page([row({ bill: bill({ status: "failed", effective_status: "failed" }), state: state({ status: "failed", terminal: true, error_code: "113", can_retry: false, retry_at: null }) })]),
    );
    renderPage();
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
    });
    expect(listMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the page it has when a background read fails", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    listMock
      .mockResolvedValueOnce(page([row({ bill: bill({}), state: state({}) })]))
      .mockRejectedValue(new Error("gateway timeout"));
    renderPage();
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      vi.advanceTimersByTime(15_000);
      await Promise.resolve();
    });

    expect(screen.getByText("Retrying — attempt 2 of 8")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("explains an empty register instead of showing nothing", async () => {
    listMock.mockResolvedValue(page([]));
    renderPage();
    await waitFor(() => expect(screen.getByText("No e-Way bills match")).toBeTruthy());
    expect(screen.getByText(/none have been raised yet/i)).toBeTruthy();
  });
});
