/**
 * Reservations: the cursor-paged list and its filters, "Expired" shown for a lapsed hold, the
 * Reserve stock flow (decimal string quantity, a document id unless manual, a refusal that shows
 * available against requested) and the per-row release (all or part, checked against what remains).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import { ITEM, location, reservation } from "@/test-utils/movementData";
import { renderScreen } from "@/test-utils/renderScreen";
import { ReservationsPage } from "./Reservations";

const listReservations = vi.fn();
const createReservation = vi.fn();
const releaseReservation = vi.fn();
const searchInventory = vi.fn();
const listWarehouses = vi.fn();
const listLocations = vi.fn();
const listUoms = vi.fn();

vi.mock("@/api/movements", () => ({
  listReservations: (...args: unknown[]) => listReservations(...args),
  createReservation: (...args: unknown[]) => createReservation(...args),
  releaseReservation: (...args: unknown[]) => releaseReservation(...args),
}));

vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  searchInventory: (...args: unknown[]) => searchInventory(...args),
  listWarehouses: (...args: unknown[]) => listWarehouses(...args),
  listLocations: (...args: unknown[]) => listLocations(...args),
  listUoms: (...args: unknown[]) => listUoms(...args),
}));

const RESERVE = "operations.inventory.stock.reserve:ORG";

beforeEach(() => {
  for (const fn of [
    listReservations,
    createReservation,
    releaseReservation,
    searchInventory,
    listWarehouses,
    listLocations,
    listUoms,
  ]) {
    fn.mockReset();
  }
  listReservations.mockResolvedValue({ items: [reservation()], next_cursor: null });
  searchInventory.mockResolvedValue([ITEM]);
  listWarehouses.mockResolvedValue({
    items: [
      { id: "wh-src", code: "WH-BHW", name: "Bhiwandi DC", location: "" },
      { id: "wh-dst", code: "WH-PUN", name: "Pune Plant", location: "" },
    ],
    total: 2,
    page: 1,
    size: 200,
    pages: 1,
  });
  listLocations.mockResolvedValue([location(), location({ id: "bin-2", code: "A-02", name: "Bin A-02" })]);
  listUoms.mockResolvedValue({ items: [], total: 0, page: 1, size: 200, pages: 0 });
});

async function openReserveDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Reserve stock" }));
  const dialog = await screen.findByRole("dialog");
  await user.type(within(dialog).getByRole("combobox", { name: "Item" }), "steel");
  await user.click(await within(dialog).findByRole("option", { name: /RM-STL-4501/ }));
  // Picking the item chooses its own warehouse; the bins of that warehouse then load.
  await waitFor(() => expect((within(dialog).getByLabelText(/^Warehouse/) as HTMLSelectElement).value).toBe("wh-src"));
  await waitFor(() => expect((within(dialog).getByLabelText(/Bin to hold from/) as HTMLSelectElement).disabled).toBe(false));
  return dialog;
}

describe("ReservationsPage list", () => {
  it("shows item, location, quantity, remaining, reference, expiry and status", async () => {
    listReservations.mockResolvedValue({
      items: [
        reservation({
          id: "r1",
          quantity: "20",
          remaining: "12.5",
          released: "7.5",
          reference_type: "order",
          reference_id: "5b6f3c1e-4a9d-4e0a-9d51-2f7a1c0b8e11",
          expires_at: "2026-10-12T18:00:00Z",
        }),
      ],
      next_cursor: null,
    });
    renderScreen(<ReservationsPage />, []);

    const row = (await screen.findByText("RM-STL-4501")).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("A-01")).toBeDefined();
    expect(within(row).getByText("20 MT")).toBeDefined();
    expect(within(row).getByText("12.5 MT")).toBeDefined();
    expect(within(row).getByText("Sales order · 5b6f3c1e")).toBeDefined();
    expect(within(row).getByText("Active")).toBeDefined();
  });

  it("shows a lapsed reservation as Expired, using expired_at, with no release action", async () => {
    listReservations.mockResolvedValue({
      items: [
        reservation({
          status: "released",
          remaining: "0",
          released: "20",
          expires_at: "2026-10-03T06:00:00Z",
          expired_at: "2026-10-03T06:05:00Z",
        }),
      ],
      next_cursor: null,
    });
    renderScreen(<ReservationsPage />, [RESERVE]);

    const row = (await screen.findByText("RM-STL-4501")).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getAllByText(/Expired/).length).toBeGreaterThan(0);
    expect(within(row).queryByText("Released")).toBeNull();
    expect(within(row).queryByRole("button", { name: /^Release Reservation/ })).toBeNull();
  });

  it("passes the status and reference filters to the server", async () => {
    const user = userEvent.setup();
    renderScreen(<ReservationsPage />, []);
    await waitFor(() => expect(listReservations).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "Active" }));
    await waitFor(() => expect(listReservations).toHaveBeenLastCalledWith(expect.objectContaining({ status: "active" })));

    await user.selectOptions(screen.getByLabelText("Reference"), "order");
    await waitFor(() =>
      expect(listReservations).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "active", reference_type: "order" }),
      ),
    );
  });

  it("loads more by cursor", async () => {
    listReservations
      .mockResolvedValueOnce({ items: [reservation({ id: "r1", sku: "AAA-1" })], next_cursor: "1" })
      .mockResolvedValueOnce({ items: [reservation({ id: "r2", sku: "BBB-2" })], next_cursor: null });
    const user = userEvent.setup();
    renderScreen(<ReservationsPage />, []);

    await user.click(await screen.findByRole("button", { name: "Load more" }));

    expect(await screen.findByText("BBB-2")).toBeDefined();
    expect(screen.getByText("AAA-1")).toBeDefined();
    expect(screen.getByText("Showing all 2 reservations")).toBeDefined();
  });

  it("has an empty state, and a different one when filters are on", async () => {
    listReservations.mockResolvedValue({ items: [], next_cursor: null });
    const user = userEvent.setup();
    renderScreen(<ReservationsPage />, []);
    expect(await screen.findByText("No reservations yet")).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Active" }));
    expect(await screen.findByText("No reservations match these filters")).toBeDefined();
  });

  it("has an error state with a retry", async () => {
    listReservations.mockRejectedValueOnce(new ApiError(500, "The server fell over", null));
    const user = userEvent.setup();
    renderScreen(<ReservationsPage />, []);
    expect(await screen.findByText("Couldn't load reservations")).toBeDefined();
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText("RM-STL-4501")).toBeDefined();
  });

  it("offers Reserve stock and Release only with the reserve permission", async () => {
    const first = renderScreen(<ReservationsPage />, [RESERVE]);
    expect(await screen.findByRole("button", { name: "Reserve stock" })).toBeDefined();
    expect(screen.getByRole("button", { name: /^Release Reservation/ })).toBeDefined();
    first.unmount();

    renderScreen(<ReservationsPage />, []);
    await screen.findByText("RM-STL-4501");
    expect(screen.queryByRole("button", { name: "Reserve stock" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Release Reservation/ })).toBeNull();
  });
});

describe("Reserve stock", () => {
  it("creates a manual hold, sending the quantity as a decimal string with an idempotency key", async () => {
    createReservation.mockResolvedValue(reservation({ quantity: "12.5", remaining: "12.5" }));
    const user = userEvent.setup();
    renderScreen(<ReservationsPage />, [RESERVE]);
    const dialog = await openReserveDialog(user);

    await user.selectOptions(within(dialog).getByLabelText(/Bin to hold from/), "bin-src");
    await user.type(within(dialog).getByLabelText("Quantity"), "12.5");
    await user.click(within(dialog).getByRole("button", { name: "Reserve stock" }));

    await waitFor(() => expect(createReservation).toHaveBeenCalledTimes(1));
    const [body, key] = createReservation.mock.calls[0] as [Record<string, unknown>, string];
    expect(body).toEqual({
      inventory_item_id: "item-1",
      location_id: "bin-src",
      quantity: "12.5",
      reference_type: "manual",
      note: "",
    });
    expect(typeof body.quantity).toBe("string");
    expect(typeof key).toBe("string");
    // The dialog closes and the list is reloaded.
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(listReservations.mock.calls.length).toBeGreaterThan(1);
  });

  it("asks for the order's id unless the hold is manual", async () => {
    const user = userEvent.setup();
    renderScreen(<ReservationsPage />, [RESERVE]);
    const dialog = await openReserveDialog(user);

    await user.selectOptions(within(dialog).getByLabelText(/Bin to hold from/), "bin-src");
    await user.type(within(dialog).getByLabelText("Quantity"), "3");
    await user.selectOptions(within(dialog).getByLabelText("Held for"), "order");
    await user.click(within(dialog).getByRole("button", { name: "Reserve stock" }));

    expect(await within(dialog).findByText("Enter the id of the order this is held for.")).toBeDefined();
    expect(createReservation).not.toHaveBeenCalled();
  });

  it("shows available against requested, and the holds in the way, when the stock is not there", async () => {
    createReservation.mockRejectedValue(
      new ApiError(409, "x", {
        type: "https://errors.opsgrids.dev/insufficient-stock",
        status: 409,
        detail: "Cannot take 80 MT",
        errors: [
          {
            sku: "RM-STL-4501",
            location: "A-01",
            uom: "MT",
            requested: "80",
            on_hand: "82.5",
            reserved: "10",
            available: "72.5",
            reservations: [{ reservation_id: "r9", reference_type: "order", reference_id: "o1", quantity: "10" }],
          },
        ] as never,
      }),
    );
    const user = userEvent.setup();
    renderScreen(<ReservationsPage />, [RESERVE]);
    const dialog = await openReserveDialog(user);

    await user.selectOptions(within(dialog).getByLabelText(/Bin to hold from/), "bin-src");
    await user.type(within(dialog).getByLabelText("Quantity"), "80");
    await user.click(within(dialog).getByRole("button", { name: "Reserve stock" }));

    expect(await within(dialog).findByText("Not enough stock to reserve this")).toBeDefined();
    expect(
      within(dialog).getByText(/you asked for 80 MT, but only 72.5 MT is available \(82.5 MT on hand, 10 MT already reserved\)/),
    ).toBeDefined();
    expect(within(dialog).getByText(/10 MT reserved for an order/)).toBeDefined();
    // What was typed is still there to correct.
    expect((within(dialog).getByLabelText("Quantity") as HTMLInputElement).value).toBe("80");
  });
});

describe("Release", () => {
  async function openRelease(user: ReturnType<typeof userEvent.setup>) {
    listReservations.mockResolvedValue({
      items: [reservation({ quantity: "20", remaining: "20" })],
      next_cursor: null,
    });
    renderScreen(<ReservationsPage />, [RESERVE]);
    await user.click(await screen.findByRole("button", { name: /^Release Reservation/ }));
    return screen.findByRole("dialog");
  }

  it("releases everything still reserved with an empty body", async () => {
    releaseReservation.mockResolvedValue({ ...reservation(), status: "released", remaining: "0" });
    const user = userEvent.setup();
    const dialog = await openRelease(user);

    await user.click(within(dialog).getByRole("button", { name: "Release stock" }));

    await waitFor(() => expect(releaseReservation).toHaveBeenCalledTimes(1));
    const [id, body, key] = releaseReservation.mock.calls[0] as [string, unknown, string];
    expect(id).toBe("res-1");
    expect(body).toEqual({});
    expect(typeof key).toBe("string");
  });

  it("releases part of it as a decimal string", async () => {
    releaseReservation.mockResolvedValue({ ...reservation(), remaining: "14.5" });
    const user = userEvent.setup();
    const dialog = await openRelease(user);

    await user.click(within(dialog).getByRole("radio", { name: /Part of it/ }));
    await user.type(within(dialog).getByLabelText("Quantity to release"), "5.5");
    await user.click(within(dialog).getByRole("button", { name: "Release stock" }));

    await waitFor(() => expect(releaseReservation).toHaveBeenCalledTimes(1));
    expect(releaseReservation.mock.calls[0]?.[1]).toEqual({ quantity: "5.5" });
  });

  it("will not release more than remains", async () => {
    const user = userEvent.setup();
    const dialog = await openRelease(user);

    await user.click(within(dialog).getByRole("radio", { name: /Part of it/ }));
    await user.type(within(dialog).getByLabelText("Quantity to release"), "20.5");
    await user.click(within(dialog).getByRole("button", { name: "Release stock" }));

    expect(await within(dialog).findByText("Only 20 MT is still reserved.")).toBeDefined();
    expect(releaseReservation).not.toHaveBeenCalled();
  });
});
