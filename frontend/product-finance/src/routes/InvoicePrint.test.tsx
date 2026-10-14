import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GST_REGISTRATION, INVOICES } from "@/api/fixtures";

const registration = vi.hoisted(() => ({ mode: "present" as "present" | "none" }));

vi.mock("@/api/client", async () => {
  const fixtures = await import("@/api/fixtures");
  const customers = await import("@/data/customers");
  const { ApiError: Err } = await import("@opsgrids/client");
  return {
    getInvoice: async (id: string) => {
      const found = fixtures.INVOICES.find((i) => i.id === id);
      if (!found) throw new Error("not found");
      return found;
    },
    getCustomer: async (id: string) => {
      const found = customers.CUSTOMERS.find((c) => c.id === id);
      if (!found) throw new Error("not found");
      return found;
    },
    getGstRegistration: async () => {
      if (registration.mode === "none") {
        throw new Err(404, "Not found", null);
      }
      return fixtures.GST_REGISTRATION;
    },
    listFilings: async () => [],
  };
});

import { InvoicePrintPage } from "./InvoicePrint";

const sample = INVOICES.find((i) => i.document_type === "invoice");

function renderPage() {
  if (!sample) throw new Error("fixture invoice missing");
  return render(
    <MemoryRouter initialEntries={[`/invoices/${sample.id}/print`]}>
      <Routes>
        <Route path="/invoices/:id/print" element={<InvoicePrintPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("InvoicePrintPage", () => {
  beforeEach(() => {
    registration.mode = "present";
  });

  it("offers Download PDF and Print once the document is ready", async () => {
    renderPage();
    await screen.findByRole("heading", { level: 1, name: "Tax Invoice" });
    const download = screen.getByRole("button", { name: "Download PDF" });
    expect(download.hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("button", { name: "Print" }).hasAttribute("disabled")).toBe(false);
    expect(screen.getByText(GST_REGISTRATION.legal_name)).toBeTruthy();
  });

  it("asks for the registration instead of printing without a supplier name", async () => {
    registration.mode = "none";
    renderPage();
    expect(await screen.findByText("Fill this in before sending")).toBeTruthy();
    expect(screen.getByText(/GST registration is not set up/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open GST registration settings" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Download PDF" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Print" }).hasAttribute("disabled")).toBe(true);
  });
});
