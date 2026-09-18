/**
 * The card speaks one of two languages and must not mix them.
 *
 * The case worth keeping forever is the last one in the ETA block: a trade line that *has* a
 * delivery estimate still shows no delivery chip. Fixtures get widened, importers get sloppy, and
 * the day a trade store starts promising "delivery in 12 minutes" is the day a customer relies on
 * it. The gate is mode *and* data, never either alone.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PRODUCTS } from "@/api/fixtures";
import type { Product, ProductVariant } from "@/api/types";
import { deliveryEtaLabel, discountPercent } from "@/lib/format";
import type { StoreMode } from "../schema";
import { ProductCard } from "./ProductCard";

const NAME = "Alpine Salted Butter 500 g";

function variant(id: string, label: string, overrides: Partial<ProductVariant> = {}): ProductVariant {
  return {
    id,
    label,
    price_min_minor: 6900,
    price_max_minor: 6900,
    mrp_minor: 9900,
    in_stock: true,
    ...overrides,
  };
}

/** A line built off a real fixture, so it stays honest about every field the contract carries. */
function product(overrides: Partial<Product> = {}): Product {
  const base = PRODUCTS[0];
  if (!base) throw new Error("the product fixtures are empty");
  return {
    ...base,
    id: "test-line",
    code: "TEST-001",
    name: NAME,
    spec: "Cultured, 82% fat, refrigerated",
    price_min_minor: 6900,
    price_max_minor: 6900,
    moq: 500,
    moq_unit: "pcs",
    image_url: null,
    mrp_minor: null,
    delivery_eta_minutes: null,
    variants: [],
    ...overrides,
  };
}

function renderCard(mode: StoreMode, overrides: Partial<Product> = {}) {
  return render(<ProductCard product={product(overrides)} mode={mode} />);
}

describe("the primary action", () => {
  const cases: ReadonlyArray<readonly [StoreMode, string, string]> = [
    ["retail", "ADD", "Add to line list"],
    ["trade", "Add to line list", "ADD"],
  ];

  it.each(cases)("in %s mode offers %s and not %s", (mode, present, absent) => {
    renderCard(mode);

    expect(screen.getByRole("button", { name: `${present}: ${NAME}` })).not.toBeNull();
    expect(screen.queryByRole("button", { name: new RegExp(`^${absent}:`) })).toBeNull();
  });

  it.each(cases)("names itself with the product in %s mode", (mode) => {
    renderCard(mode);

    for (const button of screen.getAllByRole("button")) {
      expect(button.getAttribute("aria-label")).toContain(NAME);
    }
  });
});

describe("the meta row", () => {
  it("carries availability and MOQ in trade", () => {
    renderCard("trade");

    expect(screen.getByText(/MOQ 500 pcs/)).not.toBeNull();
  });

  it("carries no MOQ in retail — a shopper buys one", () => {
    renderCard("retail");

    expect(screen.queryByText(/MOQ/)).toBeNull();
  });

  it("shows the delivery chip in retail when there is an estimate", () => {
    const line = product({ delivery_eta_minutes: 12 });
    const label = deliveryEtaLabel(line);
    if (label === null) throw new Error("a line with an ETA must have an ETA label");

    render(<ProductCard product={line} mode="retail" />);

    expect(screen.getByText(label)).not.toBeNull();
  });

  it("shows no delivery chip in retail when there is no estimate", () => {
    renderCard("retail", { delivery_eta_minutes: null });

    expect(screen.queryByText(/min/i)).toBeNull();
  });

  it("shows no delivery chip in trade even when the data is set", () => {
    const line = product({ delivery_eta_minutes: 12 });
    const label = deliveryEtaLabel(line);
    if (label === null) throw new Error("a line with an ETA must have an ETA label");

    render(<ProductCard product={line} mode="trade" />);

    expect(screen.queryByText(label)).toBeNull();
  });
});

describe("the discount flag", () => {
  it("appears in retail when the MRP is genuinely above the price", () => {
    const line = product({ mrp_minor: 9900 });
    const percent = discountPercent(line);
    if (percent === null) throw new Error("₹99 down to ₹69 is a real saving");

    render(<ProductCard product={line} mode="retail" />);

    expect(screen.getByText(`${percent}% OFF`)).not.toBeNull();
  });

  it("stays away when there is no saving to claim", () => {
    const line = product({ mrp_minor: null });
    expect(discountPercent(line)).toBeNull();

    render(<ProductCard product={line} mode="retail" />);

    expect(screen.queryByText(/% OFF/)).toBeNull();
  });

  it("stays away in trade even when an MRP is set", () => {
    render(<ProductCard product={product({ mrp_minor: 9900 })} mode="trade" />);

    expect(screen.queryByText(/% OFF/)).toBeNull();
  });
});

describe("the price", () => {
  it("reads as one sentence with the MRP behind it", () => {
    render(<ProductCard product={product({ mrp_minor: 9900 })} mode="retail" />);

    expect(screen.getByLabelText(/^₹69, was ₹99$/)).not.toBeNull();
    expect(document.querySelector("s")?.textContent).toBe("₹99");
  });
});

describe("the pack-size selector", () => {
  /* `variants[0]` is the default pack and the product's own numbers equal it — the contract's
     invariant, kept here so the fixture reads like something the API could return. */
  const two = [
    variant("v1", "500 g"),
    variant("v2", "1 kg", { price_min_minor: 12900, price_max_minor: 12900, mrp_minor: 17900 }),
  ];
  const multipack = { variants: two, mrp_minor: 9900 };

  it("appears in retail once there are two packs to choose between", async () => {
    render(<ProductCard product={product(multipack)} mode="retail" />);

    const select = screen.getByLabelText(`Pack size, ${NAME}`);
    expect(select.tagName).toBe("SELECT");

    await userEvent.selectOptions(select, "v2");
    expect((select as HTMLSelectElement).value).toBe("v2");
  });

  it("stays away for a single-pack line", () => {
    render(<ProductCard product={product({ variants: [variant("v1", "500 g")] })} mode="retail" />);

    expect(screen.queryByLabelText(`Pack size, ${NAME}`)).toBeNull();
  });

  it("stays away when there are no variants at all", () => {
    renderCard("retail");

    expect(screen.queryByLabelText(`Pack size, ${NAME}`)).toBeNull();
  });

  it("stays away in trade, which orders by MOQ and not by pack", () => {
    render(<ProductCard product={product(multipack)} mode="trade" />);

    expect(screen.queryByLabelText(`Pack size, ${NAME}`)).toBeNull();
  });
});

describe("the card styles", () => {
  it("writes the spec line in detail", () => {
    render(<ProductCard product={product()} mode="retail" cardStyle="detail" />);

    expect(screen.getByText("Cultured, 82% fat, refrigerated")).not.toBeNull();
  });

  it("drops the spec line in compact, where a phone rail has no room for it", () => {
    render(<ProductCard product={product()} mode="retail" cardStyle="compact" />);

    expect(screen.queryByText("Cultured, 82% fat, refrigerated")).toBeNull();
    expect(screen.getByRole("heading", { name: NAME })).not.toBeNull();
  });
});

describe("the code line", () => {
  it("is written in trade, where a buyer quotes it on a PO", () => {
    renderCard("trade");

    expect(screen.getByText(/TEST-001/)).not.toBeNull();
  });

  it("is hidden in retail, where nobody quotes a part code", () => {
    renderCard("retail");

    expect(screen.queryByText(/TEST-001/)).toBeNull();
  });
});
