/**
 * The requisition form: quantities are decimal strings validated against the item's unit (whole
 * numbers for a discrete unit like pcs), and nothing is sent until the form is clean.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { inventoryItemOut } from "@/data/importExport/entityTestKit";
import { renderRoute } from "@/test-utils/renderRoute";
import { PurchaseRequisitionFormPage } from "./PurchaseRequisitionForm";

const createRequisition = vi.fn();
const getRequisition = vi.fn();
const updateRequisition = vi.fn();
const listUoms = vi.fn();
const listWarehouses = vi.fn();
const searchOrderableItems = vi.fn();

vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  createRequisition: (...args: unknown[]) => createRequisition(...args),
  getRequisition: (...args: unknown[]) => getRequisition(...args),
  updateRequisition: (...args: unknown[]) => updateRequisition(...args),
  listUoms: (...args: unknown[]) => listUoms(...args),
  listWarehouses: (...args: unknown[]) => listWarehouses(...args),
  searchOrderableItems: (...args: unknown[]) => searchOrderableItems(...args),
}));

const PCS_ITEM = inventoryItemOut({ id: "item-1", sku: "BOLT-1", name: "Hex bolt", uom: "pcs" });
const METRE_ITEM = inventoryItemOut({ id: "item-2", sku: "FAB-1", name: "Poplin", uom: "m" });

function renderForm() {
  return renderRoute(
    [
      { path: "/purchase-requisitions/new", element: <PurchaseRequisitionFormPage /> },
      { path: "/purchase-requisitions/:id", element: <h1>Requisition detail</h1> },
      { path: "/purchase-requisitions", element: <h1>Requisitions</h1> },
    ],
    "/purchase-requisitions/new",
    [],
  );
}

async function chooseItem(user: ReturnType<typeof userEvent.setup>, query: string, optionName: RegExp) {
  await user.type(screen.getByRole("combobox", { name: "Item" }), query);
  await waitFor(() => expect(screen.getByRole("option", { name: optionName })).toBeDefined());
  await user.keyboard("{ArrowDown}{Enter}");
}

describe("PurchaseRequisitionFormPage", () => {
  beforeEach(() => {
    createRequisition.mockReset();
    listUoms.mockResolvedValue({
      items: [
        { id: "u1", code: "pcs", name: "Pieces", dimension: "count", is_discrete: true, is_system: true },
        { id: "u2", code: "m", name: "Metres", dimension: "length", is_discrete: false, is_system: true },
      ],
      total: 2,
      page: 1,
      size: 200,
      pages: 1,
    });
    listWarehouses.mockResolvedValue({ items: [{ id: "wh-1", code: "WH", name: "Bhiwandi DC", location: "" }], total: 1, page: 1, size: 200, pages: 1 });
    searchOrderableItems.mockImplementation(async (q: string) => (q.toLowerCase().startsWith("bol") ? [PCS_ITEM] : [METRE_ITEM]));
  });

  it("names what is missing and sends nothing", async () => {
    const user = userEvent.setup();
    renderForm();
    await screen.findByRole("heading", { name: "New purchase requisition" });

    await user.click(screen.getByRole("button", { name: "Create requisition" }));

    expect(await screen.findByText("Choose the item to request.")).toBeDefined();
    expect(screen.getByText("Enter how much is needed.")).toBeDefined();
    expect(createRequisition).not.toHaveBeenCalled();
  });

  it("asks for a whole number when the item's unit is discrete", async () => {
    const user = userEvent.setup();
    renderForm();
    await waitFor(() => expect(listUoms).toHaveBeenCalled());
    await chooseItem(user, "bol", /BOLT-1/);

    await user.type(screen.getByRole("textbox", { name: /Quantity/ }), "2.5");
    await user.click(screen.getByRole("button", { name: "Create requisition" }));

    expect(await screen.findByText("pcs come in whole units — enter a whole number.")).toBeDefined();
    expect(createRequisition).not.toHaveBeenCalled();
  });

  it("refuses more than three decimals and a zero quantity", async () => {
    const user = userEvent.setup();
    renderForm();
    await chooseItem(user, "fab", /FAB-1/);

    const quantity = screen.getByRole("textbox", { name: /Quantity/ });
    await user.type(quantity, "1.2345");
    await user.click(screen.getByRole("button", { name: "Create requisition" }));
    expect(await screen.findByText("Enter a quantity with at most three decimals, like 82.5.")).toBeDefined();

    await user.clear(quantity);
    await user.type(quantity, "0");
    await user.click(screen.getByRole("button", { name: "Create requisition" }));
    expect(await screen.findByText("Enter a quantity above zero.")).toBeDefined();
    expect(createRequisition).not.toHaveBeenCalled();
  });

  it("creates a draft with a decimal-string quantity and opens it", async () => {
    createRequisition.mockResolvedValue({ id: "req-9", number: "PR-0009" });
    const user = userEvent.setup();
    const router = renderForm();
    await chooseItem(user, "fab", /FAB-1/);

    await user.type(screen.getByRole("textbox", { name: /Quantity/ }), "82.5");
    await user.click(screen.getByRole("button", { name: "Create requisition" }));

    await waitFor(() => expect(createRequisition).toHaveBeenCalledTimes(1));
    expect(createRequisition).toHaveBeenCalledWith({
      requested_by: "Asha Rao",
      lines: [{ inventory_item_id: "item-2", quantity: "82.5" }],
    });
    await waitFor(() => expect(router.state.location.pathname).toBe("/purchase-requisitions/req-9"));
  });
});
