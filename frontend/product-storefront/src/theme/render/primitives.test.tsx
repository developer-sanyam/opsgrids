/**
 * The primitives the retail card is built from.
 *
 * Every value here has to come out of a theme token, so these tests assert the *shape* of what a
 * primitive emits — a real `<select>`, a real `<s>`, a name a screen reader can use — and never a
 * literal colour or pixel, which is the merchant's data and not ours to pin.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ActionButton, ActionLink, DiscountBadge, PriceWithMrp, VariantSelect } from "./primitives";

describe("action size", () => {
  it("defaults to md, so nothing already built moves", () => {
    render(<ActionButton>Send enquiry</ActionButton>);

    expect(screen.getByRole("button").style.padding).toBe(
      "calc(var(--st-space) * 0.8) calc(var(--st-space) * 1.3)",
    );
  });

  it("reaches the compact size a card needs", () => {
    render(
      <ActionButton type="button" size="sm">
        ADD
      </ActionButton>,
    );

    expect(screen.getByRole("button").style.padding).toBe(
      "calc(var(--st-space) * 0.45) calc(var(--st-space) * 0.85)",
    );
  });

  it("reaches links too, and both sizes stay on the theme's own scale", () => {
    render(
      <MemoryRouter>
        <ActionLink href="/store/catalogue" size="sm">
          View all
        </ActionLink>
      </MemoryRouter>,
    );

    const padding = screen.getByRole("link").style.padding;
    expect(padding).toContain("var(--st-space)");
    expect(padding).not.toMatch(/\d+px/);
  });
});

describe("a fuller accessible name", () => {
  it("keeps the visible words inside it, so voice control still works", () => {
    render(
      <ActionButton type="button" ariaLabel="ADD: Alpine Salted Butter 500 g">
        ADD
      </ActionButton>,
    );

    const button = screen.getByRole("button", { name: "ADD: Alpine Salted Butter 500 g" });
    expect(button.textContent).toBe("ADD");
  });

  it("is absent rather than empty when nobody passes one", () => {
    render(<ActionButton type="button">ADD</ActionButton>);

    expect(screen.getByRole("button").hasAttribute("aria-label")).toBe(false);
  });
});

describe("the discount flag", () => {
  it("writes the saving out, so the tint is never the only thing carrying it", () => {
    render(<DiscountBadge percent={27} />);

    expect(screen.getByText("27% OFF")).not.toBeNull();
  });
});

describe("price against MRP", () => {
  it("reads as one sentence and strikes the MRP in the markup", () => {
    const { container } = render(<PriceWithMrp price="₹69" mrp="₹99" />);

    expect(screen.getByLabelText("₹69, was ₹99")).not.toBeNull();
    expect(container.querySelector("s")?.textContent).toBe("₹99");
  });

  it("says 'was' in the markup too, so the pair is never silent", () => {
    const { container } = render(<PriceWithMrp price="₹69" mrp="₹99" />);

    expect(container.textContent).toBe("₹69, was₹99");
    expect(container.querySelector("[aria-hidden]")).toBeNull();
  });

  it("says only the price when there is no MRP to strike", () => {
    const { container } = render(<PriceWithMrp price="₹69" mrp={null} />);

    expect(screen.getByLabelText("₹69")).not.toBeNull();
    expect(container.querySelector("s")).toBeNull();
  });
});

describe("the variant selector", () => {
  const options = [
    { value: "v1", label: "500 g" },
    { value: "v2", label: "1 kg" },
    { value: "v3", label: "5 kg", disabled: true },
  ];

  it("is a real select, named by a real label", async () => {
    const onChange = vi.fn();
    render(<VariantSelect label="Pack size" value="v1" options={options} onChange={onChange} />);

    const select = screen.getByLabelText("Pack size");
    expect(select.tagName).toBe("SELECT");

    await userEvent.selectOptions(select, "v2");
    expect(onChange).toHaveBeenCalledWith("v2");
  });

  it("keeps its name when the label has no room to show, without a CSS hiding trick", () => {
    render(<VariantSelect label="Pack size, Alpine Salted Butter" value="v1" options={options} hideLabel />);

    expect(screen.getByLabelText("Pack size, Alpine Salted Butter")).not.toBeNull();
    expect(screen.queryByText("Pack size, Alpine Salted Butter")).toBeNull();
  });

  it("marks an out-of-stock pack as unselectable rather than hiding it", () => {
    render(<VariantSelect label="Pack size" value="v1" options={options} hideLabel />);

    expect(screen.getByRole("option", { name: "5 kg" }).hasAttribute("disabled")).toBe(true);
  });
});
