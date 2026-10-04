/**
 * Adjustments: the list with status in words and a Withdraw for what is still waiting, and the
 * request dialog — whose result says whether the adjustment was applied (201) or submitted for
 * approval (202, with its number and who it is waiting for), and which asks for evidence only when
 * the server says the size needs it.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import { adjustment, ITEM, location } from "@/test-utils/movementData";
import { renderScreen } from "@/test-utils/renderScreen";
import { AdjustmentsPage } from "./Adjustments";

const listAdjustments = vi.fn();
const createAdjustment = vi.fn();
const cancelAdjustment = vi.fn();
const searchInventory = vi.fn();
const listWarehouses = vi.fn();
const listLocations = vi.fn();
const listUoms = vi.fn();

vi.mock("@/api/movements", () => ({
  listAdjustments: (...args: unknown[]) => listAdjustments(...args),
  createAdjustment: (...args: unknown[]) => createAdjustment(...args),
  cancelAdjustment: (...args: unknown[]) => cancelAdjustment(...args),
}));

vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  searchInventory: (...args: unknown[]) => searchInventory(...args),
  listWarehouses: (...args: unknown[]) => listWarehouses(...args),
  listLocations: (...args: unknown[]) => listLocations(...args),
  listUoms: (...args: unknown[]) => listUoms(...args),
}));

const ADJUST = "operations.inventory.stock.adjust:ORG";

const PENDING = adjustment({
  status: "PENDING_APPROVAL",
  approval_required: true,
  approver_name: "Rohan Malhotra",
  quantity_delta: "-80",
  evidence_document_id: "0b8f3a52-6f1d-4e0c-8a3e-3b7c5d9e1f20",
});

beforeEach(() => {
  for (const fn of [listAdjustments, createAdjustment, cancelAdjustment, searchInventory, listWarehouses, listLocations, listUoms]) {
    fn.mockReset();
  }
  listAdjustments.mockResolvedValue({ items: [PENDING], next_cursor: null });
  searchInventory.mockResolvedValue([ITEM]);
  listWarehouses.mockResolvedValue({
    items: [{ id: "wh-src", code: "WH-BHW", name: "Bhiwandi DC", location: "" }],
    total: 1,
    page: 1,
    size: 200,
    pages: 1,
  });
  listLocations.mockResolvedValue([location()]);
  listUoms.mockResolvedValue({ items: [], total: 0, page: 1, size: 200, pages: 0 });
});

describe("AdjustmentsPage list", () => {
  it("shows status in words and who a held adjustment is waiting for", async () => {
    listAdjustments.mockResolvedValue({
      items: [
        PENDING,
        adjustment({ id: "a2", number: "ADJ-000011", status: "APPLIED" }),
        adjustment({ id: "a3", number: "ADJ-000010", status: "REJECTED", decided_by_name: "Meera Nair", decision_comment: "No evidence" }),
      ],
      next_cursor: null,
    });
    renderScreen(<AdjustmentsPage />, []);

    expect(await screen.findByText("ADJ-000012")).toBeDefined();
    expect(screen.getAllByText("Waiting for approval").length).toBeGreaterThan(0);
    expect(screen.getByText("Waiting for Rohan Malhotra")).toBeDefined();
    const applied = screen.getByText("ADJ-000011").closest("tr");
    const rejected = screen.getByText("ADJ-000010").closest("tr");
    if (!applied || !rejected) throw new Error("no rows");
    expect(within(applied).getByText("Applied")).toBeDefined();
    expect(within(rejected).getByText("Rejected")).toBeDefined();
    expect(within(rejected).getByText("By Meera Nair. No evidence")).toBeDefined();
    expect(screen.getByText("-80 MT")).toBeDefined();
  });

  it("filters by status on the server", async () => {
    const user = userEvent.setup();
    renderScreen(<AdjustmentsPage />, []);
    await waitFor(() => expect(listAdjustments).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Applied" }));
    await waitFor(() => expect(listAdjustments).toHaveBeenLastCalledWith(expect.objectContaining({ status: "APPLIED" })));
  });

  it("has empty, error and permission-gated states", async () => {
    listAdjustments.mockResolvedValueOnce({ items: [], next_cursor: null });
    const first = renderScreen(<AdjustmentsPage />, []);
    expect(await screen.findByText("No adjustments yet")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Request adjustment" })).toBeNull();
    first.unmount();

    listAdjustments.mockRejectedValueOnce(new ApiError(500, "The server fell over", null));
    const user = userEvent.setup();
    const second = renderScreen(<AdjustmentsPage />, [ADJUST]);
    expect(await screen.findByText("Couldn't load adjustments")).toBeDefined();
    expect(screen.getByRole("button", { name: "Request adjustment" })).toBeDefined();
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText("ADJ-000012")).toBeDefined();
    second.unmount();
  });

  it("offers Withdraw only for a pending adjustment, and only with the adjust permission", async () => {
    listAdjustments.mockResolvedValue({
      items: [PENDING, adjustment({ id: "a2", number: "ADJ-000011", status: "APPLIED" })],
      next_cursor: null,
    });
    const first = renderScreen(<AdjustmentsPage />, [ADJUST]);
    await screen.findByText("ADJ-000011");
    expect(screen.getAllByRole("button", { name: /^Withdraw / })).toHaveLength(1);
    first.unmount();

    renderScreen(<AdjustmentsPage />, []);
    await screen.findByText("ADJ-000011");
    expect(screen.queryByRole("button", { name: /^Withdraw / })).toBeNull();
  });

  it("withdraws a pending adjustment, with an optional reason", async () => {
    cancelAdjustment.mockResolvedValue(adjustment({ status: "CANCELLED" }));
    const user = userEvent.setup();
    renderScreen(<AdjustmentsPage />, [ADJUST]);

    await user.click(await screen.findByRole("button", { name: /^Withdraw / }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Withdraw adjustment" }));

    await waitFor(() => expect(cancelAdjustment).toHaveBeenCalledWith("adj-1", {}));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

describe("Request an adjustment", () => {
  async function openDialog(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("button", { name: "Request adjustment" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByRole("combobox", { name: "Item" }), "steel");
    await user.click(await within(dialog).findByRole("option", { name: /RM-STL-4501/ }));
    await waitFor(() => expect((within(dialog).getByLabelText(/Bin to adjust/) as HTMLSelectElement).disabled).toBe(false));
    await user.selectOptions(within(dialog).getByLabelText(/Bin to adjust/), "bin-src");
    await user.selectOptions(within(dialog).getByLabelText("Reason"), "lost");
    return dialog;
  }

  it("says the adjustment was applied when the server applied it", async () => {
    createAdjustment.mockResolvedValue(adjustment({ status: "APPLIED", quantity_delta: "-5" }));
    const user = userEvent.setup();
    renderScreen(<AdjustmentsPage />, [ADJUST]);
    const dialog = await openDialog(user);

    await user.type(within(dialog).getByLabelText("Quantity change"), "-5");
    await user.click(within(dialog).getByRole("button", { name: "Submit adjustment" }));

    const result = await screen.findByRole("dialog", { name: "Adjustment recorded" });
    expect(within(result).getByText("ADJ-000012 was applied")).toBeDefined();
    expect(within(result).getByText("Applied")).toBeDefined();
    expect(within(result).queryByText(/waiting for/i)).toBeNull();

    const [body, key] = createAdjustment.mock.calls[0] as [Record<string, unknown>, string];
    expect(body).toMatchObject({
      inventory_item_id: "item-1",
      location_id: "bin-src",
      quantity_delta: "-5",
      reason: "lost",
    });
    expect(typeof body.quantity_delta).toBe("string");
    expect(typeof key).toBe("string");
    expect(body).not.toHaveProperty("evidence_document_id");
  });

  it("says it was submitted for approval, with the number and who it is waiting for, when it was held", async () => {
    createAdjustment.mockResolvedValue(PENDING);
    const user = userEvent.setup();
    renderScreen(<AdjustmentsPage />, [ADJUST]);
    const dialog = await openDialog(user);

    await user.type(within(dialog).getByLabelText("Quantity change"), "-80");
    await user.click(within(dialog).getByRole("button", { name: "Submit adjustment" }));

    const result = await screen.findByRole("dialog", { name: "Submitted for approval" });
    expect(within(result).getByText("ADJ-000012 is waiting for Rohan Malhotra")).toBeDefined();
    expect(within(result).getByText("Waiting for approval")).toBeDefined();
    expect(within(result).getByText(/The stock has not changed yet/)).toBeDefined();
  });

  it("falls back to the approval service when no approver is named", async () => {
    createAdjustment.mockResolvedValue({ ...PENDING, approver_name: null });
    const user = userEvent.setup();
    renderScreen(<AdjustmentsPage />, [ADJUST]);
    const dialog = await openDialog(user);

    await user.type(within(dialog).getByLabelText("Quantity change"), "-80");
    await user.click(within(dialog).getByRole("button", { name: "Submit adjustment" }));

    expect(await screen.findByText("ADJ-000012 is waiting for the approval service")).toBeDefined();
  });

  it("shows the evidence field as required when the server asks for it, and sends the document id on the next try", async () => {
    createAdjustment
      .mockRejectedValueOnce(
        new ApiError(422, "x", {
          type: "https://errors.opsgrids.dev/validation",
          status: 422,
          detail: "An adjustment this size needs approval, and approval needs evidence",
          errors: [
            {
              field: "evidence_document_id",
              code: "required",
              message: "An adjustment this size needs approval, and approval needs evidence: attach a document.",
            },
          ],
        }),
      )
      .mockResolvedValueOnce(PENDING);
    const user = userEvent.setup();
    renderScreen(<AdjustmentsPage />, [ADJUST]);
    const dialog = await openDialog(user);

    await user.type(within(dialog).getByLabelText("Quantity change"), "-80");
    await user.click(within(dialog).getByRole("button", { name: "Submit adjustment" }));

    expect(
      await within(dialog).findByText("An adjustment this size needs approval, and approval needs evidence: attach a document."),
    ).toBeDefined();
    const evidence = within(dialog).getByLabelText(/Evidence document id/) as HTMLInputElement;
    expect(evidence.getAttribute("aria-invalid")).toBe("true");
    // What was typed is kept.
    expect((within(dialog).getByLabelText("Quantity change") as HTMLInputElement).value).toBe("-80");

    await user.type(evidence, "0b8f3a52-6f1d-4e0c-8a3e-3b7c5d9e1f20");
    await user.click(within(dialog).getByRole("button", { name: "Submit adjustment" }));

    await screen.findByRole("dialog", { name: "Submitted for approval" });
    expect(createAdjustment.mock.calls[1]?.[0]).toMatchObject({
      evidence_document_id: "0b8f3a52-6f1d-4e0c-8a3e-3b7c5d9e1f20",
      quantity_delta: "-80",
    });
    // A changed request is a new submission: it must not reuse the first one's key.
    expect(createAdjustment.mock.calls[1]?.[1]).not.toBe(createAdjustment.mock.calls[0]?.[1]);
  });

  it("refuses a zero change and a missing reason before sending", async () => {
    const user = userEvent.setup();
    renderScreen(<AdjustmentsPage />, [ADJUST]);
    await user.click(await screen.findByRole("button", { name: "Request adjustment" }));
    const dialog = await screen.findByRole("dialog");

    await user.type(within(dialog).getByLabelText("Quantity change"), "0");
    await user.click(within(dialog).getByRole("button", { name: "Submit adjustment" }));

    expect(await within(dialog).findByText("The quantity can't be zero.")).toBeDefined();
    expect(within(dialog).getByText("Choose a reason.")).toBeDefined();
    expect(within(dialog).getByText("Choose an item.")).toBeDefined();
    expect(createAdjustment).not.toHaveBeenCalled();
  });

  it("shows what is available when taking stock out would go below what is there", async () => {
    createAdjustment.mockRejectedValue(
      new ApiError(409, "x", {
        type: "https://errors.opsgrids.dev/insufficient-stock",
        status: 409,
        detail: "Cannot take 100 MT",
        errors: [
          { sku: "RM-STL-4501", location: "A-01", uom: "MT", requested: "10", on_hand: "5", reserved: "0", available: "5", reservations: [] },
        ] as never,
      }),
    );
    const user = userEvent.setup();
    renderScreen(<AdjustmentsPage />, [ADJUST]);
    const dialog = await openDialog(user);

    await user.type(within(dialog).getByLabelText("Quantity change"), "-10");
    await user.click(within(dialog).getByRole("button", { name: "Submit adjustment" }));

    expect(await within(dialog).findByText("Not enough stock to take this out")).toBeDefined();
    expect(within(dialog).getByText(/you asked for 10 MT, but only 5 MT is available/)).toBeDefined();
  });
});
