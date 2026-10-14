import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { INVOICES } from "@/api/fixtures";

vi.mock("@/api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/client")>();
  const fixtures = await import("@/api/fixtures");
  return {
    ...original,
    getInvoice: async (id: string) => {
      const found = fixtures.INVOICES.find((i) => i.id === id);
      if (!found) throw new Error("not found");
      return found;
    },
    listFilings: async () => [],
  };
});
vi.mock("@/api/customers", async () => {
  const customers = await import("@/data/customers");
  return { getCustomerById: async () => customers.FIXTURE_CUSTOMERS };
});

import { InvoiceDetailPage } from "./InvoiceDetail";

describe("InvoiceDetailPage line items", () => {
  it("labels every figure on a line so a phone can stack the row instead of scrolling the table", async () => {
    const sample = INVOICES.find((i) => i.document_type === "invoice" && i.lines.length > 0);
    if (!sample) throw new Error("fixture invoice missing");
    render(
      <MemoryRouter initialEntries={[`/invoices/${sample.id}`]}>
        <Routes>
          <Route path="/invoices/:id" element={<InvoiceDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );
    const table = await screen.findByRole("table", { name: `Line items for ${sample.number}` });
    expect(table.classList.contains("stack-rows")).toBe(true);
    const firstRow = within(table).getAllByRole("row")[1];
    if (!firstRow) throw new Error("no body row");
    const labels = Array.from(firstRow.querySelectorAll("td[data-label]")).map((cell) =>
      cell.getAttribute("data-label"),
    );
    expect(labels).toEqual(["Qty", "Unit price", "Amount"]);
    expect(firstRow.querySelector("td.stack-title")?.textContent).toBe(sample.lines[0]?.description);
  });
});
