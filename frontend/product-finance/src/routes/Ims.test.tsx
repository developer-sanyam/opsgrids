import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { PermissionProvider } from "@opsgrids/client";

import type { ImsDocument, ImsPush, ImsPushSummary, Page } from "@/api/types";
import { ImsPage } from "./Ims";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  summary: vi.fn(),
  bulk: vi.fn(),
  push: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  // The sample-data chip reads this to decide whether to announce itself.
  USE_FIXTURES: false,
  listImsDocuments: mocks.list,
  getImsPushSummary: mocks.summary,
  decideImsDocumentsBulk: mocks.bulk,
  requestImsPush: mocks.push,
}));

vi.mock("@opsgrids/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("@opsgrids/client")>();
  return { ...original, useAuth: () => ({ user: { full_name: "Priya Nair" } }) };
});

const EVERYTHING = [
  "finance.compliance.ims.read:ORG",
  "finance.compliance.ims.decide:ORG",
  "finance.compliance.ims.push:ORG",
] as const;
const READ_ONLY = ["finance.compliance.ims.read:ORG"] as const;

function push(over: Partial<ImsPush>): ImsPush {
  return {
    id: "push-1",
    decision_id: "dec-1",
    ims_document_id: "d-1",
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

function doc(id: string, number: string, over: Partial<ImsDocument> = {}): ImsDocument {
  return {
    id,
    source_ref: `ref-${id}`,
    source: "gstn",
    supplier_gstin: "27AAAAA0000A1Z5",
    supplier_name: `Vendor ${number}`,
    document_number: number,
    document_type: "invoice",
    document_date: "2026-08-10",
    period: "2026-08",
    taxable_minor: 100_000,
    cgst_minor: 9_000,
    sgst_minor: 9_000,
    igst_minor: 0,
    total_minor: 118_000,
    irn: null,
    status: "pending",
    fetched_at: "2026-09-01T00:00:00Z",
    ...over,
  };
}

function page(items: ImsDocument[]): Page<ImsDocument> {
  return { items, total: items.length, page: 1, size: 25, pages: 1 };
}

function summary(over: Partial<ImsPushSummary> = {}): ImsPushSummary {
  return {
    period: "2026-08",
    queued: 2,
    requested: 0,
    submitting: 0,
    pushed: 1,
    failed: 0,
    conflict: 0,
    drift: 0,
    pushable: 2,
    unqueued: 0,
    ...over,
  };
}

const INITIAL = [
  doc("d-1", "INV-A"),
  doc("d-2", "INV-B", { status: "accepted", push: push({ ims_document_id: "d-2", status: "pushed" }) }),
  doc("d-3", "INV-C", {
    status: "rejected",
    push: push({ ims_document_id: "d-3", action: "reject", status: "failed", error_code: "IMS201", error: "not on the portal" }),
  }),
];

function renderPage(grants: readonly string[] = EVERYTHING) {
  return render(
    <MemoryRouter initialEntries={["/compliance/ims"]}>
      <PermissionProvider grants={grants}>
        <Routes>
          <Route path="/compliance/ims" element={<ImsPage />} />
          <Route path="/compliance/ims/:id" element={<p>the detail page</p>} />
        </Routes>
      </PermissionProvider>
    </MemoryRouter>,
  );
}

function chooseAugust() {
  fireEvent.change(screen.getByLabelText(/Return period/), { target: { value: "2026-08" } });
}

beforeEach(() => {
  mocks.list.mockReset();
  mocks.summary.mockReset();
  mocks.bulk.mockReset();
  mocks.push.mockReset();
  mocks.list.mockResolvedValue(page(INITIAL));
  mocks.summary.mockResolvedValue(summary());
  mocks.bulk.mockResolvedValue([]);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("the queue", () => {
  it("shows each row's decision and where it stands with GSTN, in words", async () => {
    renderPage();
    await screen.findByText("INV-A");

    // The filter pills use the same words; the badges are the ones inside the table.
    const table = within(screen.getByRole("table"));
    expect(table.getByText("On GSTN")).toBeTruthy();
    expect(table.getByText("Send failed")).toBeTruthy();
    expect(table.getByText("Code IMS201 — not on the portal")).toBeTruthy();
  });

  it("gives every row its own labelled reason field", async () => {
    renderPage();
    await screen.findByText("INV-A");
    for (const number of ["INV-A", "INV-B", "INV-C"]) {
      expect(screen.getByLabelText(new RegExp(`Reason for ${number}`))).toBeTruthy();
    }
  });

  it("filters by where the delivery stands, and by drift as its own", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("INV-A");

    await user.click(screen.getByRole("button", { name: "Send failed" }));
    await waitFor(() =>
      expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ pushStatus: "failed", page: 1 })),
    );
    await user.click(screen.getByRole("button", { name: "GSTN differs" }));
    await waitFor(() =>
      expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ pushStatus: "drift" })),
    );
  });

  it("filters by return period only once it is a whole month", async () => {
    renderPage();
    await screen.findByText("INV-A");
    fireEvent.change(screen.getByLabelText(/Return period/), { target: { value: "2026-0" } });
    expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ period: "" }));
    chooseAugust();
    await waitFor(() =>
      expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ period: "2026-08" })),
    );
  });
});

describe("opening a document", () => {
  it("opens it from a click on the row", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByText("INV-A"));
    expect(await screen.findByText("the detail page")).toBeTruthy();
  });

  it("does not open it when a reason is typed or a row ticked", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("INV-A");

    await user.type(screen.getByLabelText(/Reason for INV-A/), "a b");
    await user.click(screen.getByLabelText("Select Vendor INV-A INV-A"));

    expect(screen.queryByText("the detail page")).toBeNull();
    expect((screen.getByLabelText(/Reason for INV-A/) as HTMLInputElement).value).toBe("a b");
  });
});

describe("deciding several at once", () => {
  it("offers nothing until something is ticked, then says how many", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("INV-A");
    expect(screen.queryByRole("region", { name: "Decide the selected documents" })).toBeNull();

    await user.click(screen.getByLabelText("Select Vendor INV-A INV-A"));
    await user.click(screen.getByLabelText("Select Vendor INV-B INV-B"));

    const bar = screen.getByRole("region", { name: "Decide the selected documents" });
    expect(within(bar).getByText("2 documents selected")).toBeTruthy();
  });

  it("records each ticked row with the reason typed beside it, then says what it did", async () => {
    mocks.bulk.mockResolvedValue([
      { document_id: "d-1", decision: {} },
      { document_id: "d-2", decision: {} },
    ]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("INV-A");

    await user.type(screen.getByLabelText(/Reason for INV-A/), "Wrong recipient");
    await user.type(screen.getByLabelText(/Reason for INV-B/), "Duplicate of INV-9");
    await user.click(screen.getByLabelText("Select Vendor INV-A INV-A"));
    await user.click(screen.getByLabelText("Select Vendor INV-B INV-B"));
    await user.click(screen.getByRole("button", { name: "Reject…" }));
    await user.click(await screen.findByRole("button", { name: "Reject" }));

    await waitFor(() => expect(mocks.bulk).toHaveBeenCalledTimes(1));
    expect(mocks.bulk).toHaveBeenCalledWith(
      [
        { document_id: "d-1", action: "reject", note: "Wrong recipient" },
        { document_id: "d-2", action: "reject", note: "Duplicate of INV-9" },
      ],
      "Priya Nair",
    );

    const receipt = await screen.findByRole("status");
    expect(receipt.textContent).toMatch(/Recorded 2 decisions — reject/);
    expect(receipt.textContent).toMatch(/nothing is sent until someone with the push permission/);
    // The selection is spent, the reasons are cleared, and the queue re-read.
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Decide the selected documents" })).toBeNull(),
    );
    expect((screen.getByLabelText(/Reason for INV-A/) as HTMLInputElement).value).toBe("");
    expect(mocks.list.mock.calls.length).toBeGreaterThan(1);
  });

  it("refuses a reject with no reason before it asks anything", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("INV-A");

    await user.click(screen.getByLabelText("Select Vendor INV-A INV-A"));
    await user.click(screen.getByRole("button", { name: "Reject…" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText(/Still missing one for: INV-A\./)).toBeTruthy();
    expect(mocks.bulk).not.toHaveBeenCalled();
  });

  it("drops the selection when the filter changes, so an action cannot reach rows no longer shown", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("INV-A");
    await user.click(screen.getByLabelText("Select Vendor INV-A INV-A"));
    expect(screen.getByRole("region", { name: "Decide the selected documents" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Accepted" }));

    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Decide the selected documents" })).toBeNull(),
    );
  });

  it("shows a reader the selection but not the buttons that decide it", async () => {
    const user = userEvent.setup();
    renderPage(READ_ONLY);
    await screen.findByText("INV-A");
    await user.click(screen.getByLabelText("Select Vendor INV-A INV-A"));

    expect(screen.queryByRole("button", { name: "Accept…" })).toBeNull();
    expect(screen.getByText(/finance\.compliance\.ims\.decide/)).toBeTruthy();
  });
});

describe("sending a period", () => {
  it("asks for a period first, and reads no counts without one", async () => {
    renderPage();
    await screen.findByText("INV-A");
    expect(screen.getByText(/Choose a return period above/)).toBeTruthy();
    expect(mocks.summary).not.toHaveBeenCalled();
  });

  it("reads the period's counts and offers to send what is waiting", async () => {
    renderPage();
    await screen.findByText("INV-A");
    chooseAugust();

    expect(await screen.findByRole("button", { name: "Send 2 decisions to GSTN…" })).toBeTruthy();
    expect(mocks.summary).toHaveBeenCalledWith("2026-08");
  });

  it("does not offer the send to someone who cannot", async () => {
    renderPage(["finance.compliance.ims.read:ORG", "finance.compliance.ims.decide:ORG"]);
    await screen.findByText("INV-A");
    chooseAugust();

    await screen.findByText(/You can see where August 2026 stands but not send it/);
    expect(screen.queryByRole("button", { name: /Send \d+ decision/ })).toBeNull();
  });

  it("sends the period on confirmation, then reports how many were asked for", async () => {
    mocks.push.mockResolvedValue({ period: "2026-08", requested: 2, not_pushable: 1 });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("INV-A");
    chooseAugust();

    await user.click(await screen.findByRole("button", { name: "Send 2 decisions to GSTN…" }));
    await user.click(await screen.findByRole("button", { name: "Send to GSTN" }));

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("2026-08"));
    const receipts = await screen.findAllByRole("status");
    expect(receipts.some((node) => /Asked for 2 decisions for August 2026/.test(node.textContent ?? ""))).toBe(true);
  });
});

describe("watching a push", () => {
  it("re-reads quietly while one is under way, and stops when it has settled", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    mocks.summary
      .mockResolvedValueOnce(summary({ requested: 2 }))
      .mockResolvedValue(summary({ queued: 0, requested: 0, pushed: 3, pushable: 0 }));
    mocks.list
      .mockResolvedValueOnce(page(INITIAL))
      .mockResolvedValue(page([doc("d-1", "INV-A", { status: "accepted", push: push({ status: "pushed" }) })]));

    const { container } = renderPage();
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.change(screen.getByLabelText(/Return period/), { target: { value: "2026-08" } });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText(/Sending — 2 still under way/)).toBeTruthy();
    const summaryCalls = mocks.summary.mock.calls.length;

    await act(async () => {
      vi.advanceTimersByTime(6_000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.summary.mock.calls.length).toBe(summaryCalls + 1);
    expect(screen.queryByText(/Sending — /)).toBeNull();
    // No skeletons in between: a poll must not tear down what a person is typing into.
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBe(0);

    // Settled: no further reads however long it is left.
    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
    });
    expect(mocks.summary.mock.calls.length).toBe(summaryCalls + 1);
  });
});
