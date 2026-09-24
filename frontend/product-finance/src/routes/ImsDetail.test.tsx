import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { PermissionProvider } from "@opsgrids/client";

import type { ImsDecision, ImsDocumentDetail, ImsPush } from "@/api/types";
import { ImsDetailPage } from "./ImsDetail";

const mocks = vi.hoisted(() => ({ get: vi.fn(), decide: vi.fn() }));

vi.mock("@/api/client", () => ({
  USE_FIXTURES: false,
  getImsDocument: mocks.get,
  decideImsDocument: mocks.decide,
}));

vi.mock("@opsgrids/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("@opsgrids/client")>();
  return { ...original, useAuth: () => ({ user: { full_name: "Priya Nair" } }) };
});

function push(over: Partial<ImsPush>): ImsPush {
  return {
    id: "push-1",
    decision_id: "dec-1",
    ims_document_id: "d-1",
    period: "2026-08",
    action: "accept",
    version: 1,
    status: "pushed",
    is_current: false,
    requested_at: "2026-09-02T00:00:00Z",
    attempts: 1,
    max_attempts: 6,
    next_attempt_at: null,
    pushed_at: "2026-09-02T00:01:00Z",
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

function decision(id: string, version: number, over: Partial<ImsDecision>): ImsDecision {
  return {
    id,
    action: "accept",
    note: null,
    suggested_action: null,
    suggestion_reason: null,
    expense_category: "general",
    decided_by: "Priya Nair",
    decided_at: "2026-09-02T00:00:00Z",
    version,
    ...over,
  };
}

const DETAIL: ImsDocumentDetail = {
  document: {
    id: "d-1",
    source_ref: "ref-1",
    source: "gstn",
    supplier_gstin: "27AAAAA0000A1Z5",
    supplier_name: "Vendor One",
    document_number: "INV-A",
    document_type: "invoice",
    document_date: "2026-08-10",
    period: "2026-08",
    taxable_minor: 100_000,
    cgst_minor: 9_000,
    sgst_minor: 9_000,
    igst_minor: 0,
    total_minor: 118_000,
    irn: null,
    status: "rejected",
    fetched_at: "2026-09-01T00:00:00Z",
    push: push({ decision_id: "dec-2", action: "reject", version: 2, status: "failed", is_current: true, error_code: "IMS201", error: "not on the portal" }),
  },
  decisions: [
    decision("dec-2", 2, {
      action: "reject",
      note: "Duplicate",
      push: push({ decision_id: "dec-2", action: "reject", version: 2, status: "failed", is_current: true, error_code: "IMS201", error: "not on the portal" }),
    }),
    decision("dec-1", 1, {
      push: push({ status: "pushed", drift: true, observed_action: "pending" }),
    }),
  ],
};

function renderDetail(grants: readonly string[] = ["finance.compliance.ims.decide:ORG"]) {
  return render(
    <MemoryRouter initialEntries={["/compliance/ims/d-1"]}>
      <PermissionProvider grants={grants}>
        <Routes>
          <Route path="/compliance/ims/:id" element={<ImsDetailPage />} />
        </Routes>
      </PermissionProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mocks.get.mockReset();
  mocks.decide.mockReset();
  mocks.get.mockResolvedValue(DETAIL);
});

describe("a document's decision history", () => {
  it("numbers each decision and shows where each stands with GSTN", async () => {
    renderDetail();
    const list = within(await screen.findByRole("list"));

    expect(list.getByText("v2")).toBeTruthy();
    expect(list.getByText("v1")).toBeTruthy();
    expect(list.getByText("Send failed")).toBeTruthy();
    expect(list.getByText("Code IMS201 — not on the portal")).toBeTruthy();
  });

  it("shows an older decision that GSTN has since disagreed with, in the portal's words", async () => {
    renderDetail();
    const list = within(await screen.findByRole("list"));
    expect(list.getByText("GSTN differs")).toBeTruthy();
    expect(list.getByText("You sent accepted; the last sync found GSTN showing on hold.")).toBeTruthy();
  });
});

describe("deciding", () => {
  it("offers taking a decision back, saying what that does to the credit", async () => {
    renderDetail();
    const option = await screen.findByRole("radio", { name: /Take no action/ });
    expect(option).toBeTruthy();
    expect(screen.getByText(/any credit it claimed is taken back out/)).toBeTruthy();
  });

  it("records a withdrawal as a decision, like any other", async () => {
    mocks.decide.mockResolvedValue(decision("dec-3", 3, { action: "no_action" }));
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("radio", { name: /Take no action/ }));
    await user.click(screen.getByRole("button", { name: "Record decision" }));

    await waitFor(() => expect(mocks.decide).toHaveBeenCalledTimes(1));
    expect(mocks.decide).toHaveBeenCalledWith(
      "d-1",
      expect.objectContaining({ action: "no_action", decidedBy: "Priya Nair" }),
    );
  });

  it("does not offer the form to someone who cannot decide", async () => {
    renderDetail(["finance.compliance.ims.read:ORG"]);
    await screen.findByText(/deciding is a separate permission/);
    expect(screen.queryByRole("button", { name: "Record decision" })).toBeNull();
  });
});
