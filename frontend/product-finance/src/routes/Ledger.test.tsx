import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { LedgerReport } from "@/api/types";

const report = vi.hoisted(() => ({ current: null as LedgerReport | null }));

vi.mock("@/api/client", () => ({
  getLedger: async () => report.current,
}));

import { LedgerPage } from "./Ledger";

function renderPage() {
  return render(
    <MemoryRouter>
      <LedgerPage />
    </MemoryRouter>,
  );
}

describe("LedgerPage", () => {
  it("says the reports are not built and shows no figures and no export when there is no ledger", async () => {
    report.current = { period_label: "Not available yet", as_of: "2026-10-15", accounts: [], sample: true };
    renderPage();

    expect(await screen.findByText("Ledger reports are not available yet")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Export CSV" })).toBeNull();
    expect(screen.queryByText(/₹/)).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows the trial balance and its export when the report has real accounts", async () => {
    report.current = {
      period_label: "FY 2026-27",
      as_of: "2026-10-15",
      accounts: [{ code: "4000", name: "Sales", type: "revenue", debit_minor: 0, credit_minor: 100000 }],
    };
    renderPage();

    expect(await screen.findByRole("table")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeTruthy();
    expect(screen.queryByText("Ledger reports are not available yet")).toBeNull();
  });
});
