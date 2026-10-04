/**
 * Receiving goods: quantity rules and the short / over hints, roll totals that must add up, the
 * forced quality hold, one Idempotency-Key per attempt (reused on retry), server problems landing
 * on the line they name, and the permission gate.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";

import type { ReceivingContextOut, ReceivingLineOut } from "@/api/types";
import { renderRoute } from "@/test-utils/renderRoute";
import { ReceiveGoodsPage } from "./ReceiveGoods";

const getReceivingContext = vi.fn();
const postGoodsReceipt = vi.fn();
const listWarehouses = vi.fn();
const listLocations = vi.fn();

vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  getReceivingContext: (...args: unknown[]) => getReceivingContext(...args),
  postGoodsReceipt: (...args: unknown[]) => postGoodsReceipt(...args),
  listWarehouses: (...args: unknown[]) => listWarehouses(...args),
  listLocations: (...args: unknown[]) => listLocations(...args),
}));

const GRANTS = ["operations.procurement.receipt.write:ORG", "operations.inventory.stock.receive:ORG"];

function line(overrides: Partial<ReceivingLineOut> = {}): ReceivingLineOut {
  return {
    purchase_order_line_id: "line-1",
    sku: "FAB-100",
    description: "Cotton poplin",
    inventory_item_id: "item-1",
    stock_uom: "m",
    ordered_qty: "100",
    received_qty: "20",
    remaining_qty: "80",
    max_receivable_qty: "82",
    tolerance_pct: "2.5",
    short_closed: false,
    track_lot: false,
    track_roll: false,
    track_expiry: false,
    track_quality: false,
    default_location_id: "loc-1",
    default_location_label: "REC-1",
    conversions: [],
    ...overrides,
  };
}

function context(lines: ReceivingLineOut[], overrides: Partial<ReceivingContextOut> = {}): ReceivingContextOut {
  return {
    purchase_order_id: "po-1",
    po_number: "PO-0001",
    po_status: "approved",
    warehouse: "Bhiwandi DC",
    supplier_name: "Kaveri Textiles",
    can_receive: true,
    lines,
    ...overrides,
  };
}

function renderReceive(grants: string[] = GRANTS) {
  return renderRoute(
    [
      { path: "/purchase-orders/:id/receive", element: <ReceiveGoodsPage /> },
      { path: "/purchase-orders/:id", element: <h1>Purchase order detail</h1> },
      { path: "/goods-receipts/:id", element: <h1>Receipt detail</h1> },
    ],
    "/purchase-orders/po-1/receive",
    grants,
  );
}

function lineSection(name: string) {
  return within(screen.getByRole("region", { name }));
}

async function loaded() {
  await screen.findByRole("heading", { name: "Receive goods" });
}

describe("ReceiveGoodsPage", () => {
  beforeEach(() => {
    getReceivingContext.mockReset();
    postGoodsReceipt.mockReset();
    listWarehouses.mockResolvedValue({ items: [{ id: "wh-1", code: "WH", name: "Bhiwandi DC", location: "" }], total: 1, page: 1, size: 200, pages: 1 });
    listLocations.mockResolvedValue([]);
  });

  it("does not even load the order for someone without both permissions", async () => {
    getReceivingContext.mockResolvedValue(context([line()]));
    renderReceive(["operations.procurement.receipt.write:ORG"]);

    expect(await screen.findByText("You can't receive goods")).toBeDefined();
    expect(getReceivingContext).not.toHaveBeenCalled();
  });

  it("explains an order that cannot take a delivery", async () => {
    getReceivingContext.mockResolvedValue(context([line()], { can_receive: false, po_status: "closed" }));
    renderReceive();

    expect(await screen.findByText("This purchase order can't be received")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Post receipt" })).toBeNull();
  });

  it("shows what is ordered, received, remaining and the tolerance ceiling per line", async () => {
    getReceivingContext.mockResolvedValue(context([line()]));
    renderReceive();
    await loaded();

    const section = lineSection("Cotton poplin");
    expect(section.getByText("100 m")).toBeDefined();
    expect(section.getByText("20 m")).toBeDefined();
    expect(section.getByText("80 m")).toBeDefined();
    expect(section.getByText("82 m")).toBeDefined();
    expect(section.getByText("Most you can receive (2.5% tolerance)")).toBeDefined();
  });

  it("shows no inputs for a line closed short", async () => {
    getReceivingContext.mockResolvedValue(context([line({ short_closed: true })]));
    renderReceive();
    await loaded();
    expect(screen.getByText("This line was closed short. Nothing more is expected on it.")).toBeDefined();
    expect(screen.queryByRole("textbox", { name: /Quantity received/ })).toBeNull();
  });

  it("hints while short, warns above what remains, and blocks above the ceiling", async () => {
    getReceivingContext.mockResolvedValue(context([line()]));
    const user = userEvent.setup();
    renderReceive();
    await loaded();
    const quantity = lineSection("Cotton poplin").getByRole("textbox", { name: /Quantity received/ });

    await user.type(quantity, "50");
    expect(await screen.findByText("30 m would still be expected after this.")).toBeDefined();

    await user.clear(quantity);
    await user.type(quantity, "81");
    expect(await screen.findByText(/1 m over what the order still expects, within the 2.5% tolerance/)).toBeDefined();

    await user.clear(quantity);
    await user.type(quantity, "83");
    expect(await screen.findByText(/more than can be received: at most 82 m/)).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Post receipt" }));
    expect(postGoodsReceipt).not.toHaveBeenCalled();
  });

  it("refuses a malformed quantity beside the field", async () => {
    getReceivingContext.mockResolvedValue(context([line()]));
    const user = userEvent.setup();
    renderReceive();
    await loaded();

    await user.type(lineSection("Cotton poplin").getByRole("textbox", { name: /Quantity received/ }), "1.2345");
    expect(await screen.findByText("Enter a quantity with at most three decimals, like 82.5.")).toBeDefined();
  });

  it("asks for at least one line before posting", async () => {
    getReceivingContext.mockResolvedValue(context([line()]));
    const user = userEvent.setup();
    renderReceive();
    await loaded();

    await user.click(screen.getByRole("button", { name: "Post receipt" }));
    expect(await screen.findByText("Enter the quantity received on at least one line.")).toBeDefined();
    expect(postGoodsReceipt).not.toHaveBeenCalled();
  });

  it("forces quality hold on, and disabled, for an item that tracks quality, and sends it", async () => {
    getReceivingContext.mockResolvedValue(context([line({ track_quality: true })]));
    postGoodsReceipt.mockResolvedValue({ id: "grn-1", number: "GRN-0001", po_number: "PO-0001" });
    const user = userEvent.setup();
    renderReceive();
    await loaded();

    const hold = lineSection("Cotton poplin").getByRole("switch", { name: "Hold for quality" }) as HTMLButtonElement;
    expect(hold.getAttribute("aria-checked")).toBe("true");
    expect(hold.disabled).toBe(true);
    expect(screen.getByText(/always received into quality hold/)).toBeDefined();

    await user.type(lineSection("Cotton poplin").getByRole("textbox", { name: /Quantity received/ }), "10");
    await user.click(screen.getByRole("button", { name: "Post receipt" }));

    await waitFor(() => expect(postGoodsReceipt).toHaveBeenCalledTimes(1));
    expect(postGoodsReceipt.mock.calls[0]?.[0]).toMatchObject({
      lines: [{ purchase_order_line_id: "line-1", quantity: "10", hold: true }],
    });
  });

  it("blocks submit until the rolls add up exactly to the line quantity", async () => {
    getReceivingContext.mockResolvedValue(context([line({ track_roll: true })]));
    postGoodsReceipt.mockResolvedValue({ id: "grn-1", number: "GRN-0001", po_number: "PO-0001" });
    const user = userEvent.setup();
    renderReceive();
    await loaded();
    const section = lineSection("Cotton poplin");

    await user.type(section.getByRole("textbox", { name: /Quantity received/ }), "50");
    await user.click(section.getByRole("button", { name: "Add roll" }));
    const first = within(section.getByRole("group", { name: "Roll 1" }));
    await user.type(first.getByRole("textbox", { name: /Length/ }), "30");
    expect(section.getByText(/Rolls total 30 m of 50 m. They don't add up yet./)).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Post receipt" }));
    expect(await screen.findByText("The rolls add up to 30 m, but this line receives 50 m.")).toBeDefined();
    expect(postGoodsReceipt).not.toHaveBeenCalled();

    await user.click(section.getByRole("button", { name: "Add roll" }));
    const second = within(section.getByRole("group", { name: "Roll 2" }));
    await user.type(second.getByRole("textbox", { name: /Length/ }), "20");
    await user.type(second.getByRole("textbox", { name: /Measured weight/ }), "3.6");
    await user.type(second.getByRole("textbox", { name: /Weight unit/ }), "kg");
    expect(section.getByText(/Rolls total 50 m of 50 m. They add up./)).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Post receipt" }));
    await waitFor(() => expect(postGoodsReceipt).toHaveBeenCalledTimes(1));
    expect(postGoodsReceipt.mock.calls[0]?.[0]).toMatchObject({
      lines: [
        {
          quantity: "50",
          rolls: [{ primary_qty: "30" }, { primary_qty: "20", secondary_qty: "3.6", secondary_uom: "kg" }],
        },
      ],
    });
  });

  it("requires the expiry date for an item that expires", async () => {
    getReceivingContext.mockResolvedValue(context([line({ track_lot: true, track_expiry: true })]));
    const user = userEvent.setup();
    renderReceive();
    await loaded();
    const section = lineSection("Cotton poplin");

    await user.type(section.getByRole("textbox", { name: /Quantity received/ }), "10");
    await user.click(screen.getByRole("button", { name: "Post receipt" }));

    expect(await screen.findByText("This item expires: enter the expiry date.")).toBeDefined();
    expect(postGoodsReceipt).not.toHaveBeenCalled();
  });

  it("sends one Idempotency-Key per attempt, reuses it on a retry, and opens the receipt", async () => {
    getReceivingContext.mockResolvedValue(context([line()]));
    postGoodsReceipt
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({ id: "grn-1", number: "GRN-0001", po_number: "PO-0001" });
    const user = userEvent.setup();
    const router = renderReceive();
    await loaded();

    await user.type(lineSection("Cotton poplin").getByRole("textbox", { name: /Quantity received/ }), "40");
    await user.click(screen.getByRole("button", { name: "Post receipt" }));
    expect(await screen.findByText("Something went wrong. Try again.")).toBeDefined();
    // What was entered survives the failure.
    expect((lineSection("Cotton poplin").getByRole("textbox", { name: /Quantity received/ }) as HTMLInputElement).value).toBe("40");

    await user.click(screen.getByRole("button", { name: "Post receipt" }));
    await waitFor(() => expect(postGoodsReceipt).toHaveBeenCalledTimes(2));
    const [firstBody, firstKey] = postGoodsReceipt.mock.calls[0] as [unknown, string];
    const [secondBody, secondKey] = postGoodsReceipt.mock.calls[1] as [unknown, string];
    expect(firstKey).toMatch(/^[0-9a-f-]{36}$/);
    expect(secondKey).toBe(firstKey);
    expect(secondBody).toEqual(firstBody);
    expect(firstBody).toMatchObject({ purchase_order_id: "po-1", lines: [{ purchase_order_line_id: "line-1", quantity: "40" }] });
    await waitFor(() => expect(router.state.location.pathname).toBe("/goods-receipts/grn-1"));
  });

  it("uses a fresh key once the entered figures change", async () => {
    getReceivingContext.mockResolvedValue(context([line()]));
    postGoodsReceipt
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const user = userEvent.setup();
    renderReceive();
    await loaded();
    const quantity = lineSection("Cotton poplin").getByRole("textbox", { name: /Quantity received/ });

    await user.type(quantity, "40");
    await user.click(screen.getByRole("button", { name: "Post receipt" }));
    await waitFor(() => expect(postGoodsReceipt).toHaveBeenCalledTimes(1));
    await user.clear(quantity);
    await user.type(quantity, "41");
    await user.click(screen.getByRole("button", { name: "Post receipt" }));
    await waitFor(() => expect(postGoodsReceipt).toHaveBeenCalledTimes(2));

    expect(postGoodsReceipt.mock.calls[1]?.[1]).not.toBe(postGoodsReceipt.mock.calls[0]?.[1]);
  });

  it("puts a server refusal next to the line it names and keeps what was typed", async () => {
    getReceivingContext.mockResolvedValue(context([line(), line({ purchase_order_line_id: "line-2", sku: "FAB-200", description: "Linen" })]));
    postGoodsReceipt.mockRejectedValue(
      new ApiError(422, "Unprocessable", {
        type: "https://errors.opsgrids.dev/validation",
        title: "Validation failed",
        status: 422,
        detail: "Over receipt",
        errors: [{ field: "lines.0.quantity", code: "over_receipt", message: "Another delivery just landed: at most 5 m can still be received." }],
      }),
    );
    const user = userEvent.setup();
    renderReceive();
    await loaded();

    // Only the second line has a quantity, so it is line 0 of the request.
    await user.type(lineSection("Linen").getByRole("textbox", { name: /Quantity received/ }), "10");
    await user.click(screen.getByRole("button", { name: "Post receipt" }));

    expect(await lineSection("Linen").findByText("Another delivery just landed: at most 5 m can still be received.")).toBeDefined();
    expect(within(screen.getByRole("region", { name: "Cotton poplin" })).queryByText(/Another delivery/)).toBeNull();
    expect((lineSection("Linen").getByRole("textbox", { name: /Quantity received/ }) as HTMLInputElement).value).toBe("10");
  });
});
