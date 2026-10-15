import { fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SupplierSettings } from "@/api/types";

const supplier = vi.hoisted(() => ({ ready: true }));

vi.mock("@/api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/client")>();
  const customers = await import("@/data/customers");
  const fixtures = await import("@/api/fixtures");
  return {
    ...original,
    listCustomers: async () => ({
      items: customers.CUSTOMERS.map((customer) => ({ customer })),
      total: customers.CUSTOMERS.length,
      page: 1,
      size: 200,
      pages: 1,
    }),
    getSupplierSettings: async (): Promise<SupplierSettings> => {
      const settings = fixtures.fixtureSupplierSettings();
      return supplier.ready ? settings : { ...settings, ready: false, missing: ["address_line1", "pincode"] };
    },
  };
});

import * as customersData from "@/data/customers";

import { InvoiceNewPage } from "./InvoiceNew";

function stubPhone(): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

function renderPage() {
  return render(<RouterProvider router={createMemoryRouter([{ path: "*", element: <InvoiceNewPage /> }])} />);
}

beforeEach(() => {
  supplier.ready = true;
});
afterEach(() => vi.unstubAllGlobals());

function customersFirstId(): string {
  return customersData.CUSTOMERS[0]?.id ?? "";
}

describe("InvoiceNewPage", () => {
  it("keeps the total and the review button in a bar that is only for phones", async () => {
    stubPhone();
    renderPage();
    const total = await screen.findByText("Total", { selector: "span.text-caption" });
    const bar = total.closest("[data-action-bar]");
    expect(bar).not.toBeNull();
    if (!(bar instanceof HTMLElement)) throw new Error("bar missing");
    // The shared bar sits above the bottom navigation on a phone.
    expect(bar.classList.contains("shell-sticky-action")).toBe(true);

    // Reviewing from the bar validates exactly as the desktop button does.
    fireEvent.click(within(bar).getByRole("button", { name: "Review invoice" }));
    expect(await screen.findByText("Choose the customer being billed.")).toBeTruthy();
  });

  it("asks for the missing supplier details instead of offering the form", async () => {
    supplier.ready = false;
    renderPage();
    expect(await screen.findByRole("heading", { name: "Complete your supplier details first" })).toBeTruthy();
    expect(screen.getByText("Registered address")).toBeTruthy();
    expect(screen.getByText("PIN code")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Review invoice" })).toBeNull();
  });

  it("previews discount, GST and round-off the way the server computes them", async () => {
    stubPhone();
    renderPage();
    await screen.findByText("Total", { selector: "span.text-caption" });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Steel brackets" } });
    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Rate (₹)"), { target: { value: "123.45" } });
    fireEvent.change(screen.getByLabelText(/Discount/), { target: { value: "50" } });
    // 1184.50 taxable, 18 % = 213.21, 1397.71 rounds to 1398.00 (+0.29).
    expect(await screen.findByText(/Taxable value ₹1,184\.50/)).toBeTruthy();
    expect(screen.getAllByText("₹1,398.00").length).toBeGreaterThan(0);
    expect(screen.getByText("₹0.29")).toBeTruthy();
    expect(screen.getByText(/One Thousand Three Hundred Ninety-Eight Only/)).toBeTruthy();
  });

  it("keeps each line's own editor when an earlier line is removed", async () => {
    stubPhone();
    renderPage();
    await screen.findByText("Total", { selector: "span.text-caption" });
    fireEvent.click(screen.getByRole("button", { name: "Add line" }));
    const [, second] = screen.getAllByLabelText("Description");
    fireEvent.click(screen.getByRole("button", { name: "Remove line 1" }));
    // Index keys would hand line 2's state to line 1's editor and unmount this node.
    expect(second?.isConnected).toBe(true);
    expect(screen.getAllByLabelText("Description")).toHaveLength(1);
  });

  it("puts each line problem on its own field and moves focus to the first", async () => {
    stubPhone();
    renderPage();
    await screen.findByText("Total", { selector: "span.text-caption" });
    fireEvent.change(screen.getByLabelText(/Customer/), { target: { value: customersFirstId() } });
    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "abc" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Review invoice" })[0] as HTMLElement);
    const description = screen.getByLabelText("Description");
    expect(description.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByLabelText("Quantity").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByLabelText(/Rate/).getAttribute("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(description);
  });
});
