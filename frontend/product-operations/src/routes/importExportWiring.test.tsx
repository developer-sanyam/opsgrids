/**
 * How the four Operations list screens wire the Import / Export button (ADR-0022): who is offered
 * Import, what the export is scoped to, and that the list refreshes once an import lands.
 *
 * The spreadsheet engine and the dialog are other packages' business, so both are replaced here by
 * stand-ins that honour their contract: the hook reports a phase the test can move, and the button
 * renders the props it was given. What is under test is the screens' side of that contract.
 */
import { useState } from "react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type {
  AuthContextValue,
  DataExchangeState,
  ImportEntity,
  ImportPhase,
  UseImportExportOptions,
} from "@opsgrids/client";
import type { ImportExportButtonProps } from "@opsgrids/ui";

import { inventoryImport } from "@/data/importExport/inventory";
import { itemCategoryImport } from "@/data/importExport/itemCategories";
import { supplierImport } from "@/data/importExport/suppliers";
import { warehouseImport } from "@/data/importExport/warehouses";
import { InventoryPage } from "./Inventory";
import { ItemCategoriesPage } from "./ItemCategories";
import { SuppliersPage } from "./Suppliers";
import { WarehousesPage } from "./Warehouses";

const listInventory = vi.fn();
const listSuppliers = vi.fn();
const listWarehouses = vi.fn();
const listItemCategories = vi.fn();
const useImportExport = vi.fn();

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  listInventory: (...args: unknown[]) => listInventory(...args),
  listSuppliers: (...args: unknown[]) => listSuppliers(...args),
  listWarehouses: (...args: unknown[]) => listWarehouses(...args),
  listItemCategories: (...args: unknown[]) => listItemCategories(...args),
}));

vi.mock("@opsgrids/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@opsgrids/client")>()),
  useImportExport: (...args: unknown[]) => useImportExport(...args),
}));

vi.mock("@opsgrids/ui", async (importOriginal) => {
  const { createElement } = await import("react");
  return {
    ...(await importOriginal<typeof import("@opsgrids/ui")>()),
    ImportExportButton: (props: ImportExportButtonProps) =>
      createElement("div", {
        "data-testid": "import-export",
        "data-can-import": String(props.canImport),
        "data-can-export": String(props.canExport),
        "data-noun": props.meta.noun,
        "data-phase": props.state.phase,
      }),
  };
});

const AUTH: AuthContextValue = {
  status: "authenticated",
  user: {
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
  },
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

const IDLE: DataExchangeState = {
  phase: "menu",
  error: null,
  preview: null,
  progress: null,
  summary: null,
  downloadingTemplate: false,
  exporting: null,
  notice: null,
};

const EMPTY_PAGE = { items: [], total: 0, page: 1, size: 25, pages: 0 };

/** Moves the phase the stand-in hook reports, as the engine does while an import runs. */
let setPhase: (phase: ImportPhase) => void = () => undefined;

function renderScreen(page: ReactElement, grants: string[]) {
  return render(
    <AuthContext.Provider value={AUTH}>
      <PermissionProvider grants={grants} entitlements={[]}>
        <MemoryRouter>{page}</MemoryRouter>
      </PermissionProvider>
    </AuthContext.Provider>,
  );
}

function lastOptions(): UseImportExportOptions<unknown> | undefined {
  const call = useImportExport.mock.lastCall as [unknown, UseImportExportOptions<unknown> | undefined] | undefined;
  return call?.[1];
}

function lastEntity(): unknown {
  return (useImportExport.mock.lastCall as [unknown] | undefined)?.[0];
}

beforeEach(() => {
  for (const list of [listInventory, listSuppliers, listWarehouses, listItemCategories]) {
    list.mockResolvedValue(EMPTY_PAGE);
  }
  useImportExport.mockImplementation((entity: ImportEntity<unknown, unknown, unknown>) => {
    const [phase, set] = useState<ImportPhase>("menu");
    setPhase = set;
    return {
      state: { ...IDLE, phase },
      actions: {
        openFile: vi.fn(),
        startImport: vi.fn(),
        cancelImport: vi.fn(),
        reset: vi.fn(),
        downloadTemplate: vi.fn(),
        exportAll: vi.fn(),
        cancelExport: vi.fn(),
        downloadReport: vi.fn(),
      },
      meta: {
        noun: entity.noun,
        nounSingular: entity.nounSingular,
        maxRows: 1000,
        exportMaxRows: 10_000,
        requiredHeaders: [],
        canExport: true,
      },
    };
  });
});

afterEach(() => {
  setPhase = () => undefined;
});

const SCREENS = [
  {
    name: "Stock on Hand",
    element: <InventoryPage />,
    list: listInventory,
    entity: inventoryImport,
    write: "operations.inventory.item.write:ORG",
    read: "operations.inventory.item.read:ORG",
  },
  {
    name: "Suppliers",
    element: <SuppliersPage />,
    list: listSuppliers,
    entity: supplierImport,
    write: "operations.procurement.supplier.write:ORG",
    read: "operations.procurement.supplier.read:ORG",
  },
  {
    name: "Warehouses",
    element: <WarehousesPage />,
    list: listWarehouses,
    entity: warehouseImport,
    write: "operations.inventory.warehouse.write:ORG",
    read: "operations.inventory.warehouse.read:ORG",
  },
  {
    name: "Item categories",
    element: <ItemCategoriesPage />,
    list: listItemCategories,
    entity: itemCategoryImport,
    write: "operations.inventory.item.write:ORG",
    read: "operations.inventory.item.read:ORG",
  },
] as const;

describe.each(SCREENS)("$name", ({ element, list, entity, write, read }) => {
  it("drives its own entity definition, shown in the page header", async () => {
    renderScreen(element, [read, write]);

    const button = await screen.findByTestId("import-export");
    expect(button.dataset.noun).toBe(entity.noun);
    expect(lastEntity()).toBe(entity);
  });

  it("offers Import to a caller who may create records here, and Export as well", async () => {
    renderScreen(element, [read, write]);

    const button = await screen.findByTestId("import-export");
    expect(button.dataset.canImport).toBe("true");
    expect(button.dataset.canExport).toBe("true");
  });

  it("holds Import back from a caller who can only read, and still offers Export", async () => {
    renderScreen(element, [read]);

    const button = await screen.findByTestId("import-export");
    expect(button.dataset.canImport).toBe("false");
    expect(button.dataset.canExport).toBe("true");
  });

  it("holds Import back from a caller whose write access is only over their own records", async () => {
    renderScreen(element, [read, write.replace(":ORG", ":OWN")]);

    const button = await screen.findByTestId("import-export");
    expect(button.dataset.canImport).toBe("false");
  });

  it("reloads the list when an import finishes, and not before", async () => {
    renderScreen(element, [read, write]);
    await screen.findByTestId("import-export");
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1));

    act(() => setPhase("importing"));
    expect(list).toHaveBeenCalledTimes(1);

    act(() => setPhase("done"));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  });
});

describe("Stock on Hand export scope", () => {
  it("exports the list under the filters on screen, and keeps them stable between renders", async () => {
    const user = userEvent.setup();
    renderScreen(<InventoryPage />, ["operations.inventory.item.read:ORG"]);
    await screen.findByTestId("import-export");

    expect(lastOptions()).toEqual({
      filters: { q: "", warehouse: "all", item_category_id: "all", sort: "value" },
    });
    const before = lastOptions()?.filters;

    act(() => setPhase("preview"));
    expect(lastOptions()?.filters).toBe(before);

    await user.click(screen.getByRole("button", { name: "Low stock" }));
    expect(lastOptions()).toEqual({
      filters: { q: "", warehouse: "all", item_category_id: "all", sort: "value", low_stock: true },
    });

    await user.type(screen.getByLabelText("Search inventory"), "steel");
    await waitFor(() => expect(lastOptions()?.filters).toMatchObject({ q: "steel", low_stock: true }));
  });
});

describe("Suppliers export scope", () => {
  it("exports the list under the filters on screen, and keeps them stable between renders", async () => {
    const user = userEvent.setup();
    renderScreen(<SuppliersPage />, ["operations.procurement.supplier.read:ORG"]);
    await screen.findByTestId("import-export");

    expect(lastOptions()).toEqual({
      filters: { q: "", category: "all", status: "all", sort: "spend" },
    });
    const before = lastOptions()?.filters;

    act(() => setPhase("preview"));
    expect(lastOptions()?.filters).toBe(before);

    await user.selectOptions(screen.getByLabelText("Status"), "on_hold");
    await user.click(screen.getByRole("button", { name: "Packaging" }));
    expect(lastOptions()).toEqual({
      filters: { q: "", category: "packaging", status: "on_hold", sort: "spend" },
    });
  });
});
