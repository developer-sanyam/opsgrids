import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { invoiceStatusLabel } from "./invoiceStatusLabel";
import { InvoiceStatusPill } from "./StatusPill";

describe("invoice status wording", () => {
  it("calls an open, unpaid document Issued: nothing says it was sent", () => {
    expect(invoiceStatusLabel("sent", 0)).toBe("Issued");
    expect(invoiceStatusLabel("sent", undefined)).toBe("Issued");
  });

  it("says Partly paid once money is allocated to a document that is still open", () => {
    expect(invoiceStatusLabel("sent", 5000)).toBe("Partly paid");
    expect(invoiceStatusLabel("overdue", 5000)).toBe("Overdue, partly paid");
  });

  it("leaves settled and void documents as they are", () => {
    expect(invoiceStatusLabel("paid", 9000)).toBe("Paid");
    expect(invoiceStatusLabel("void", 0)).toBe("Void");
    expect(invoiceStatusLabel("draft", 0)).toBe("Draft");
  });

  it("renders the label in the badge", () => {
    render(<InvoiceStatusPill status="sent" amountPaidMinor={100} />);
    expect(screen.getByText("Partly paid")).toBeTruthy();
  });
});
