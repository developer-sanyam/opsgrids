/**
 * The new / edit transfer form: warehouses, lines (item, source bin, decimal quantity, lot and roll
 * ids only for tracked items), client checks that mirror the server's, a draft saved with an
 * idempotency key, and the permission-denied state.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { ApiError, AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";

import { ITEM, location, transfer, transferLine } from "@/test-utils/movementData";
import { TransferFormPage } from "./TransferForm";

const navigate = vi.fn();

// A data-router navigation builds a `Request` with jsdom's AbortSignal, which Node's own `Request`
// rejects; what is worth asserting is where the form sends the person, so that is what is captured.
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router-dom")>()),
  useNavigate: () => navigate,
}));

const createTransfer = vi.fn();
const getTransfer = vi.fn();
const updateTransfer = vi.fn();
const searchInventory = vi.fn();
const getInventoryItem = vi.fn();
const listWarehouses = vi.fn();
const listLocations = vi.fn();
const listUoms = vi.fn();

vi.mock("@/api/movements", () => ({
  createTransfer: (...args: unknown[]) => createTransfer(...args),
  getTransfer: (...args: unknown[]) => getTransfer(...args),
  updateTransfer: (...args: unknown[]) => updateTransfer(...args),
}));

vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  searchInventory: (...args: unknown[]) => searchInventory(...args),
  getInventoryItem: (...args: unknown[]) => getInventoryItem(...args),
  listWarehouses: (...args: unknown[]) => listWarehouses(...args),
  listLocations: (...args: unknown[]) => listLocations(...args),
  listUoms: (...args: unknown[]) => listUoms(...args),
}));

const USER: MeOut = {
  user_id: "user-1",
  email: "ops@example.com",
  full_name: "Asha Rao",
  organization_id: "org-1",
  membership_id: "mem-1",
  roles: [],
  amr: [],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [],
};

const AUTH_VALUE: AuthContextValue = {
  status: "authenticated",
  user: USER,
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

const WRITE = "operations.inventory.transfer.write:ORG";

function renderAt(path: string, grants: string[]) {
  const router = createMemoryRouter(
    [
      { path: "/inventory/transfers/new", element: <TransferFormPage /> },
      { path: "/inventory/transfers/:id/edit", element: <TransferFormPage /> },
      { path: "/inventory/transfers/:id", element: <h1>Transfer detail</h1> },
      { path: "/inventory/transfers", element: <h1>Transfers</h1> },
    ],
    { initialEntries: [path] },
  );
  render(
    <AuthContext.Provider value={AUTH_VALUE}>
      <PermissionProvider grants={grants} entitlements={[]}>
        <RouterProvider router={router} />
      </PermissionProvider>
    </AuthContext.Provider>,
  );
  return router;
}

beforeEach(() => {
  navigate.mockReset();
  for (const fn of [createTransfer, getTransfer, updateTransfer, searchInventory, getInventoryItem, listWarehouses, listLocations, listUoms]) {
    fn.mockReset();
  }
  searchInventory.mockResolvedValue([ITEM]);
  getInventoryItem.mockResolvedValue(ITEM);
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
  listLocations.mockImplementation((warehouseId: string) =>
    Promise.resolve(
      warehouseId === "wh-src"
        ? [location()]
        : [location({ id: "bin-dst", warehouse_id: "wh-dst", code: "DEFAULT", is_default: true })],
    ),
  );
  listUoms.mockResolvedValue({ items: [], total: 0, page: 1, size: 200, pages: 0 });
});

async function fillFirstLine(user: ReturnType<typeof userEvent.setup>, quantity: string) {
  await user.type(screen.getByRole("combobox", { name: "Item (line 1)" }), "steel");
  await user.click(await screen.findByRole("option", { name: /RM-STL-4501/ }));
  await waitFor(() => expect((screen.getByLabelText(/Take from bin \(line 1\)/) as HTMLSelectElement).disabled).toBe(false));
  await user.selectOptions(screen.getByLabelText(/Take from bin \(line 1\)/), "bin-src");
  await user.type(screen.getByLabelText(/Quantity \(line 1\)/), quantity);
}

describe("new transfer", () => {
  it("saves a draft with decimal-string quantities and an idempotency key, then opens it", async () => {
    createTransfer.mockResolvedValue(transfer({ id: "trf-9", number: "TRF-000009" }));
    const user = userEvent.setup();
    renderAt("/inventory/transfers/new", [WRITE]);

    await user.selectOptions(await screen.findByLabelText(/From warehouse/), "wh-src");
    await user.selectOptions(screen.getByLabelText(/To warehouse/), "wh-dst");
    await fillFirstLine(user, "12.5");
    await user.type(screen.getByRole("textbox", { name: /^Note/ }), "Urgent");
    await user.click(screen.getByRole("button", { name: "Save draft" }));

    await waitFor(() => expect(createTransfer).toHaveBeenCalledTimes(1));
    const [body, key] = createTransfer.mock.calls[0] as [{ lines: { quantity: unknown }[] }, string];
    expect(body).toEqual({
      from_warehouse_id: "wh-src",
      to_warehouse_id: "wh-dst",
      lines: [{ inventory_item_id: "item-1", from_location_id: "bin-src", quantity: "12.5" }],
      note: "Urgent",
    });
    expect(typeof body.lines[0]?.quantity).toBe("string");
    expect(typeof key).toBe("string");
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/inventory/transfers/trf-9"));
  });

  it("checks what it can before sending, each message on its own control", async () => {
    const user = userEvent.setup();
    renderAt("/inventory/transfers/new", [WRITE]);

    await user.click(await screen.findByRole("button", { name: "Save draft" }));

    expect(await screen.findByText("Choose the warehouse the stock leaves.")).toBeDefined();
    expect(screen.getByText("Choose the warehouse the stock goes to.")).toBeDefined();
    expect(screen.getByText("Choose an item.")).toBeDefined();
    expect(screen.getByText("Choose the bin it is taken from.")).toBeDefined();
    expect(screen.getByText("Enter a number with at most 3 decimal places.")).toBeDefined();
    expect(createTransfer).not.toHaveBeenCalled();
  });

  it("will not offer the source warehouse as the destination", async () => {
    const user = userEvent.setup();
    renderAt("/inventory/transfers/new", [WRITE]);
    await user.selectOptions(await screen.findByLabelText(/From warehouse/), "wh-src");
    const to = screen.getByLabelText(/To warehouse/) as HTMLSelectElement;
    expect(within(to).queryByRole("option", { name: "Bhiwandi DC" })).toBeNull();
    expect(within(to).getByRole("option", { name: "Pune Plant" })).toBeDefined();
  });

  it("asks for lot and roll ids only on a line whose item is tracked", async () => {
    searchInventory.mockResolvedValue([{ ...ITEM, track_lot: true, track_roll: false }]);
    const user = userEvent.setup();
    renderAt("/inventory/transfers/new", [WRITE]);
    await screen.findByLabelText(/From warehouse/);
    expect(screen.queryByLabelText(/Lot id/)).toBeNull();

    await user.type(screen.getByRole("combobox", { name: "Item (line 1)" }), "steel");
    await user.click(await screen.findByRole("option", { name: /RM-STL-4501/ }));

    expect(await screen.findByLabelText(/Lot id \(line 1\)/)).toBeDefined();
    expect(screen.queryByLabelText(/Roll id/)).toBeNull();
  });

  it("puts the server's field error on the line it belongs to and keeps what was typed", async () => {
    createTransfer.mockRejectedValue(
      new ApiError(422, "x", {
        type: "https://errors.opsgrids.dev/validation",
        status: 422,
        detail: "Location A-01 is not in the source warehouse",
        errors: [
          {
            field: "lines[0].from_location_id",
            code: "wrong_warehouse",
            message: "Location A-01 is not in the source warehouse",
          },
        ],
      }),
    );
    const user = userEvent.setup();
    renderAt("/inventory/transfers/new", [WRITE]);
    await user.selectOptions(await screen.findByLabelText(/From warehouse/), "wh-src");
    await user.selectOptions(screen.getByLabelText(/To warehouse/), "wh-dst");
    await fillFirstLine(user, "5");
    await user.click(screen.getByRole("button", { name: "Save draft" }));

    expect(await screen.findByText("Location A-01 is not in the source warehouse")).toBeDefined();
    expect((screen.getByLabelText(/Quantity \(line 1\)/) as HTMLInputElement).value).toBe("5");
  });

  it("adds and removes lines", async () => {
    const user = userEvent.setup();
    renderAt("/inventory/transfers/new", [WRITE]);
    await screen.findByLabelText(/From warehouse/);
    expect((screen.getByRole("button", { name: "Remove line 1" }) as HTMLButtonElement).disabled).toBe(true);

    await user.click(screen.getByRole("button", { name: "Add line" }));
    expect(screen.getByLabelText(/Quantity \(line 2\)/)).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Remove line 2" }));
    expect(screen.queryByLabelText(/Quantity \(line 2\)/)).toBeNull();
  });

  it("tells someone without the write permission why they can't use it", async () => {
    renderAt("/inventory/transfers/new", []);
    expect(await screen.findByText("You can't create or edit transfers")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Save draft" })).toBeNull();
  });
});

describe("edit transfer", () => {
  const draft = transfer({
    lines: [transferLine({ quantity: "40", lot_id: "3f2b8c1e-4a9d-4e0a-9d51-2f7a1c0b8e11" })],
    note: "First draft",
    expected_on: "2026-10-20",
  });

  it("loads the draft, and replaces its lines on save", async () => {
    getTransfer.mockResolvedValue(draft);
    updateTransfer.mockResolvedValue(draft);
    const user = userEvent.setup();
    renderAt("/inventory/transfers/trf-1/edit", [WRITE]);

    const quantity = (await screen.findByLabelText(/Quantity \(line 1\)/)) as HTMLInputElement;
    expect(quantity.value).toBe("40");
    expect((screen.getByRole("textbox", { name: /^Note/ }) as HTMLTextAreaElement).value).toBe("First draft");
    // The line already carries a lot, so the field is there even before the item is read.
    expect((screen.getByLabelText(/Lot id \(line 1\)/) as HTMLInputElement).value).toBe("3f2b8c1e-4a9d-4e0a-9d51-2f7a1c0b8e11");

    await user.clear(quantity);
    await user.type(quantity, "35.25");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateTransfer).toHaveBeenCalledTimes(1));
    expect(updateTransfer).toHaveBeenCalledWith("trf-1", {
      to_warehouse_id: "wh-dst",
      lines: [
        {
          inventory_item_id: "item-1",
          from_location_id: "bin-src",
          quantity: "35.25",
          lot_id: "3f2b8c1e-4a9d-4e0a-9d51-2f7a1c0b8e11",
        },
      ],
      note: "First draft",
      expected_on: "2026-10-20",
    });
  });

  it("refuses to edit anything that is no longer a draft", async () => {
    getTransfer.mockResolvedValue(transfer({ status: "APPROVED" }));
    renderAt("/inventory/transfers/trf-1/edit", [WRITE]);
    expect(await screen.findByText("TRF-000001 can no longer be edited")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
  });

  it("has an error state for a draft that can't be loaded", async () => {
    getTransfer.mockRejectedValue(new ApiError(404, "That transfer is not part of this organization", null));
    renderAt("/inventory/transfers/trf-1/edit", [WRITE]);
    expect(await screen.findByText("Couldn't load this transfer")).toBeDefined();
  });
});
