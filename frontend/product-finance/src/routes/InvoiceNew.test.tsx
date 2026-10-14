import { fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/client")>();
  const customers = await import("@/data/customers");
  return {
    ...original,
    listCustomers: async () => ({
      items: customers.CUSTOMERS.map((customer) => ({ customer })),
      total: customers.CUSTOMERS.length,
      page: 1,
      size: 200,
      pages: 1,
    }),
  };
});

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

afterEach(() => vi.unstubAllGlobals());

describe("InvoiceNewPage", () => {
  it("keeps the estimate and the review button in a bar that is only for phones", async () => {
    stubPhone();
    render(<RouterProvider router={createMemoryRouter([{ path: "*", element: <InvoiceNewPage /> }])} />);
    const total = await screen.findByText("Total (est.)", { selector: "span.text-xs" });
    const bar = total.closest("[data-action-bar]");
    expect(bar).not.toBeNull();
    if (!(bar instanceof HTMLElement)) throw new Error("bar missing");
    // The shared bar sits above the bottom navigation on a phone.
    expect(bar.classList.contains("shell-sticky-action")).toBe(true);

    // Reviewing from the bar validates exactly as the desktop button does.
    fireEvent.click(within(bar).getByRole("button", { name: "Review invoice" }));
    expect(await screen.findByText("Choose the customer being billed.")).toBeTruthy();
  });
});
