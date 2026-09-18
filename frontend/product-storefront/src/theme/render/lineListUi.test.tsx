/**
 * ADD, and what it is honestly worth.
 *
 * The case to keep forever is the first one: a card rendered with **no provider above it** — which
 * is the gallery's miniature, the editor's canvas and most of this suite — still paints ADD, still
 * takes a press, throws nothing and adds nothing. That no-op default is what let a context be
 * introduced into `render/` at all without breaking every tree that renders a section on its own.
 *
 * What is deliberately *not* tested here, because it does not exist: persistence, a cart route, an
 * order, a reservation, a payment. The list lives in the page and dies with it.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { PRODUCTS } from "@/api/fixtures";
import type { Product, ProductVariant } from "@/api/types";
import { LineListProvider } from "@/store/LineListProvider";
import { defaultSettings } from "../sections";
import type { StoreMode } from "../schema";
import { AnnouncementBar } from "./AnnouncementBar";
import { ProductCard } from "./ProductCard";

const NAME = "Amul Gold Full Cream Milk 1 L";
const DESKTOP_WIDTH = 1200;

function variant(id: string, label: string, price: number): ProductVariant {
  return { id, label, price_min_minor: price, price_max_minor: price, mrp_minor: null, in_stock: true };
}

function product(overrides: Partial<Product> = {}): Product {
  const base = PRODUCTS[0];
  if (!base) throw new Error("the product fixtures are empty");
  return {
    ...base,
    id: "line-a",
    code: "TEST-001",
    name: NAME,
    price_min_minor: 6900,
    price_max_minor: 6900,
    moq: 500,
    moq_unit: "pcs",
    mrp_minor: null,
    delivery_eta_minutes: null,
    variants: [],
    ...overrides,
  };
}

/** A card and the bar that counts it, under one provider — the shape `StoreRenderer` mounts. */
function renderStoreish(mode: StoreMode, overrides: Partial<Product> = {}) {
  return render(
    <MemoryRouter initialEntries={["/store"]}>
      <LineListProvider>
        <AnnouncementBar
          settings={defaultSettings("announcement-bar")}
          products={[]}
          categories={[]}
          stacked={false}
          width={DESKTOP_WIDTH}
          mode={mode}
        />
        <ProductCard product={product(overrides)} mode={mode} />
      </LineListProvider>
    </MemoryRouter>,
  );
}

function addButton(label: string) {
  return screen.getByRole("button", { name: `${label}: ${NAME}` });
}

describe("a card with no provider above it", () => {
  it("renders ADD, takes a press, throws nothing and counts nothing", async () => {
    render(<ProductCard product={product()} mode="retail" />);

    const add = screen.getByRole("button", { name: `ADD: ${NAME}` });
    await userEvent.click(add);
    await userEvent.click(add);

    // Still the same button, never a stepper: nothing was added, and nothing pretended otherwise.
    expect(screen.getByRole("button", { name: `ADD: ${NAME}` })).not.toBeNull();
    expect(screen.queryByRole("button", { name: /Add one more/ })).toBeNull();
  });

  it("says nothing to a screen reader about a press that did nothing", async () => {
    const { container } = render(<ProductCard product={product()} mode="retail" />);

    await userEvent.click(screen.getByRole("button", { name: `ADD: ${NAME}` }));

    const live = container.querySelector("[aria-live]");
    expect(live?.textContent).toBe("");
  });
});

describe("ADD, in a retail store", () => {
  it("opens a line, then steps it up and down", async () => {
    renderStoreish("retail");

    await userEvent.click(addButton("ADD"));
    expect(screen.getByRole("button", { name: `Add one more ${NAME}` })).not.toBeNull();
    expect(screen.queryByRole("button", { name: `ADD: ${NAME}` })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: `Add one more ${NAME}` }));
    expect(screen.getByText("2")).not.toBeNull();

    await userEvent.click(screen.getByRole("button", { name: `Remove one from ${NAME}` }));
    expect(screen.getByText("1")).not.toBeNull();
  });

  it("goes back to ADD when the last one is taken off", async () => {
    renderStoreish("retail");

    await userEvent.click(addButton("ADD"));
    await userEvent.click(screen.getByRole("button", { name: `Remove one from ${NAME}` }));

    expect(screen.getByRole("button", { name: `ADD: ${NAME}` })).not.toBeNull();
  });

  it("announces the quantity it actually holds, and the removal", async () => {
    const { container } = renderStoreish("retail");
    const live = () => container.querySelector("article [aria-live]")?.textContent;

    await userEvent.click(addButton("ADD"));
    expect(live()).toBe(`${NAME}, quantity 1`);

    await userEvent.click(screen.getByRole("button", { name: `Remove one from ${NAME}` }));
    expect(live()).toBe(`${NAME} removed`);
  });

  it("moves the count in the announcement bar, in items", async () => {
    renderStoreish("retail");

    expect(screen.getByText("0 items")).not.toBeNull();

    await userEvent.click(addButton("ADD"));
    expect(screen.getByText("1 item")).not.toBeNull();

    await userEvent.click(screen.getByRole("button", { name: `Add one more ${NAME}` }));
    expect(screen.getByText("2 items")).not.toBeNull();
  });

  it("leaves the count as text — there is no basket to navigate to", async () => {
    renderStoreish("retail");

    await userEvent.click(addButton("ADD"));

    for (const anchor of Array.from(document.querySelectorAll("a"))) {
      expect(anchor.textContent).not.toMatch(/\d+ items?/);
    }
  });
});

describe("the same machinery, in a trade store", () => {
  it("steps in whole MOQs and says so on both controls", async () => {
    renderStoreish("trade");

    await userEvent.click(addButton("Add to line list"));
    expect(screen.getByText("500 pcs")).not.toBeNull();

    await userEvent.click(screen.getByRole("button", { name: `Add 500 pcs of ${NAME}` }));
    expect(screen.getByText("1000 pcs")).not.toBeNull();

    await userEvent.click(screen.getByRole("button", { name: `Remove 500 pcs from ${NAME}` }));
    expect(screen.getByText("500 pcs")).not.toBeNull();
  });

  it("counts lines rather than units — a line at MOQ 500 is one line", async () => {
    renderStoreish("trade");

    await userEvent.click(addButton("Add to line list"));
    expect(screen.getByText("1 line")).not.toBeNull();
  });
});

describe("every control names its line", () => {
  const modes: ReadonlyArray<readonly [StoreMode, string]> = [
    ["retail", "ADD"],
    ["trade", "Add to line list"],
  ];

  it.each(modes)("in %s mode, before and after the line is open", async (mode, label) => {
    renderStoreish(mode);

    for (const button of screen.getAllByRole("button")) {
      if (button.closest("article") === null) continue;
      expect(button.getAttribute("aria-label")).toContain(NAME);
    }

    await userEvent.click(addButton(label));

    for (const button of screen.getAllByRole("button")) {
      if (button.closest("article") === null) continue;
      expect(button.getAttribute("aria-label")).toContain(NAME);
    }
  });
});

describe("the chosen pack", () => {
  it("is part of the line, so two packs are two lines", async () => {
    const packs = { variants: [variant("v1", "500 ml", 6900), variant("v2", "1 L", 12900)] };
    renderStoreish("retail", packs);

    await userEvent.click(addButton("ADD"));
    expect(screen.getByText("1 item")).not.toBeNull();

    // Moving the selector moves the card to the other line, which nobody has opened yet.
    await userEvent.selectOptions(screen.getByLabelText(`Pack size, ${NAME}`), "v2");
    expect(screen.getByRole("button", { name: `ADD: ${NAME}` })).not.toBeNull();

    await userEvent.click(addButton("ADD"));
    expect(screen.getByText("2 items")).not.toBeNull();
  });
});
