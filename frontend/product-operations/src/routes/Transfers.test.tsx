/**
 * The transfers list: cursor paging with "Load more", status and number filters, a labelled status
 * pill and an approval route said in words, the in-transit report with physical beside it, and all
 * four async states.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import { renderScreen } from "@/test-utils/renderScreen";
import { summary } from "@/test-utils/movementData";
import { TransfersPage } from "./Transfers";

const listTransfers = vi.fn();
const listInTransit = vi.fn();

vi.mock("@/api/movements", () => ({
  listTransfers: (...args: unknown[]) => listTransfers(...args),
  listInTransit: (...args: unknown[]) => listInTransit(...args),
}));

const WRITE = "operations.inventory.transfer.write:ORG";

beforeEach(() => {
  listTransfers.mockReset();
  listInTransit.mockReset();
  listInTransit.mockResolvedValue([]);
});

describe("TransfersPage", () => {
  it("shows each transfer with its status and approval route in words", async () => {
    listTransfers.mockResolvedValue({
      items: [
        summary({ id: "t1", number: "TRF-000003", status: "IN_TRANSIT", approval_route: "AUTO" }),
        summary({ id: "t2", number: "TRF-000002", status: "REQUESTED", approval_route: "SERVICE" }),
        summary({ id: "t3", number: "TRF-000001", status: "DRAFT", approval_route: null }),
      ],
      next_cursor: null,
    });
    renderScreen(<TransfersPage />, []);

    const row = (await screen.findByRole("link", { name: "TRF-000003" })).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("In transit")).toBeDefined();
    expect(within(row).getByText("Approved automatically")).toBeDefined();
    expect(screen.getByText("Approval service")).toBeDefined();
    expect(screen.getByText("Not requested yet")).toBeDefined();
    expect(screen.getByText("Showing all 3 transfers")).toBeDefined();
  });

  it("asks the server for the chosen status and for the number prefix", async () => {
    listTransfers.mockResolvedValue({ items: [], next_cursor: null });
    const user = userEvent.setup();
    renderScreen(<TransfersPage />, []);
    await waitFor(() => expect(listTransfers).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "In transit" }));
    await waitFor(() =>
      expect(listTransfers).toHaveBeenLastCalledWith(expect.objectContaining({ status: "IN_TRANSIT" })),
    );

    await user.type(screen.getByRole("searchbox", { name: "Search by transfer number" }), "TRF-0000");
    await waitFor(() => expect(listTransfers).toHaveBeenLastCalledWith(expect.objectContaining({ q: "TRF-0000" })));
  });

  it("appends the next page when Load more is pressed, and stops when the cursor runs out", async () => {
    listTransfers
      .mockResolvedValueOnce({ items: [summary({ id: "t1", number: "TRF-000002" })], next_cursor: "1" })
      .mockResolvedValueOnce({ items: [summary({ id: "t2", number: "TRF-000001" })], next_cursor: null });
    const user = userEvent.setup();
    renderScreen(<TransfersPage />, []);

    expect(await screen.findByText("Showing 1 transfers")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByRole("link", { name: "TRF-000001" })).toBeDefined();
    expect(screen.getByRole("link", { name: "TRF-000002" })).toBeDefined();
    expect(screen.getByText("Showing all 2 transfers")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    expect(listTransfers).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: "1" }));
  });

  it("keeps the rows on screen when a later page fails, and says so", async () => {
    listTransfers
      .mockResolvedValueOnce({ items: [summary({ id: "t1", number: "TRF-000002" })], next_cursor: "1" })
      .mockRejectedValueOnce(new ApiError(500, "The server fell over", null));
    const user = userEvent.setup();
    renderScreen(<TransfersPage />, []);

    await user.click(await screen.findByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Couldn't load more transfers")).toBeDefined();
    expect(screen.getByRole("link", { name: "TRF-000002" })).toBeDefined();
  });

  it("has an empty state that says what to do next", async () => {
    listTransfers.mockResolvedValue({ items: [], next_cursor: null });
    renderScreen(<TransfersPage />, []);
    expect(await screen.findByText("No transfers yet")).toBeDefined();
    expect(screen.getByText("Create one to move stock from one warehouse to another.")).toBeDefined();
  });

  it("has an error state with a retry that reloads", async () => {
    listTransfers.mockRejectedValueOnce(new ApiError(500, "The server fell over", null));
    listTransfers.mockResolvedValueOnce({ items: [summary()], next_cursor: null });
    const user = userEvent.setup();
    renderScreen(<TransfersPage />, []);

    expect(await screen.findByText("Couldn't load transfers")).toBeDefined();
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByRole("link", { name: "TRF-000001" })).toBeDefined();
  });

  it("offers New transfer only to someone who can write transfers", async () => {
    listTransfers.mockResolvedValue({ items: [], next_cursor: null });
    const first = renderScreen(<TransfersPage />, [WRITE]);
    expect(await screen.findByRole("button", { name: "New transfer" })).toBeDefined();
    first.unmount();

    renderScreen(<TransfersPage />, []);
    await screen.findByText("No transfers yet");
    expect(screen.queryByRole("button", { name: "New transfer" })).toBeNull();
  });

  it("reports in-transit and physical stock in separate columns, never summed", async () => {
    listTransfers.mockResolvedValue({ items: [], next_cursor: null });
    listInTransit.mockResolvedValue([
      {
        inventory_item_id: "item-1",
        sku: "RM-STL-4501",
        item_name: "Cold-rolled steel coil",
        uom: "MT",
        warehouse_id: "wh-dst",
        warehouse_code: "WH-PUN",
        in_transit: "30",
        physical: "12.5",
      },
    ]);
    renderScreen(<TransfersPage />, []);

    const row = (await screen.findByText("WH-PUN")).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("30 MT")).toBeDefined();
    expect(within(row).getByText("12.5 MT")).toBeDefined();
    expect(within(row).queryByText("42.5 MT")).toBeNull();
    expect(screen.getByRole("columnheader", { name: "In transit" })).toBeDefined();
    expect(screen.getByRole("columnheader", { name: "On hand there now" })).toBeDefined();
  });

  it("has empty and error states for the in-transit report too", async () => {
    listTransfers.mockResolvedValue({ items: [], next_cursor: null });
    listInTransit.mockRejectedValueOnce(new ApiError(500, "no report", null));
    renderScreen(<TransfersPage />, []);
    expect(await screen.findByText("Couldn't load stock in transit")).toBeDefined();
  });

  it("says nothing is in transit when nothing is", async () => {
    listTransfers.mockResolvedValue({ items: [], next_cursor: null });
    renderScreen(<TransfersPage />, []);
    expect(await screen.findByText("Nothing is in transit")).toBeDefined();
  });
});
