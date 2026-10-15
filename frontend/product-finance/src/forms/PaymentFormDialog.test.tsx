/**
 * The Reference field says when it is optional (2026-10-15 customer retest: it could be left
 * blank for cash, but nothing on the label said so).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  MAX_PAGE_SIZE: 100,
  listInvoices: async () => ({ items: [], total: 0, page: 1, size: 100 }),
  recordPayment: vi.fn(),
}));

import { PaymentFormDialog } from "./PaymentFormDialog";

describe("PaymentFormDialog: Reference", () => {
  it("is labelled optional when it is, and required when it is not", async () => {
    const user = userEvent.setup();
    render(
      <PaymentFormDialog customers={[{ value: "c1", label: "Acme" }]} onClose={vi.fn()} onSaved={vi.fn()} />,
    );

    // A bank transfer needs its UTR.
    expect(screen.getByLabelText("Reference")).toBeTruthy();

    await user.selectOptions(screen.getByLabelText(/^method/i), "cash");
    expect(screen.getByLabelText("Reference (optional)")).toBeTruthy();
  });
});
