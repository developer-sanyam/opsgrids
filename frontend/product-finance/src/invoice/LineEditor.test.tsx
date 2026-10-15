import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LineEditor } from "./LineEditor";
import { blankLine } from "./lineDraft";

function editor(props: { negativeQuantity?: boolean; error?: string } = {}) {
  return (
    <LineEditor
      index={0}
      line={blankLine()}
      figures={{ taxable: 100_00, tax: 18_00 }}
      canPickItems={false}
      canRemove
      negativeQuantity={props.negativeQuantity ?? false}
      onChange={vi.fn()}
      onRemove={vi.fn()}
      errorFor={(field) => (field === "quantity" ? props.error : undefined)}
    />
  );
}

describe("LineEditor", () => {
  it("uses the numeric pad for an invoice and a text keyboard for a credit note, which needs a minus key", () => {
    const { rerender } = render(editor());
    expect(screen.getByLabelText("Quantity").getAttribute("inputmode")).toBe("decimal");
    rerender(editor({ negativeQuantity: true }));
    expect(screen.getByLabelText("Quantity").getAttribute("inputmode")).toBe("text");
  });

  it("shows a quantity problem on the quantity field", () => {
    render(editor({ error: "Enter a quantity." }));
    expect(screen.getByLabelText("Quantity").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText("Enter a quantity.")).toBeTruthy();
  });

  it("gives Remove a 44px hit area and keeps the per-line figure out of a live region", () => {
    render(editor());
    expect(screen.getByRole("button", { name: "Remove line 1" }).className).toContain("hit-44");
    expect(screen.getByText(/Taxable value/).getAttribute("aria-live")).toBeNull();
  });
});
