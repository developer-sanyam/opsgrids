/**
 * Stock on Hand keeps Import / Export as its own button from 640px and moves it into the header's
 * "More actions" menu below that. The dialog is the real one; the data-exchange hook is a stand-in
 * that reports an idle session, because what is under test is how the screen launches it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderScreen } from "@/test-utils/renderScreen";
import { InventoryPage } from "./Inventory";

const listInventory = vi.fn();

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  listInventory: (...args: unknown[]) => listInventory(...args),
}));

vi.mock("@opsgrids/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@opsgrids/client")>()),
  useImportExport: () => ({
    state: {
      phase: "menu",
      mode: "add",
      error: null,
      preview: null,
      progress: null,
      summary: null,
      downloadingTemplate: false,
      exporting: null,
      notice: null,
      cooldownSecondsLeft: null,
    },
    actions: {
      setMode: async () => undefined,
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
      noun: "inventory items",
      nounSingular: "inventory item",
      maxRows: 1000,
      exportMaxRows: 10_000,
      requiredHeaders: [],
      canExport: true,
      canUpdate: false,
      recordIdHeader: "Record ID",
    },
  }),
}));

const GRANTS = ["operations.inventory.item.read:ORG", "operations.inventory.item.write:ORG"];

function stubViewport(wide: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: wide,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

beforeEach(() => {
  listInventory.mockResolvedValue({ items: [], total: 0, page: 1, size: 25, pages: 0 });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Stock on Hand: Import / Export in the header", () => {
  it("is its own button from 640px", async () => {
    stubViewport(true);
    renderScreen(<InventoryPage />, GRANTS);

    expect(await screen.findByRole("button", { name: "Import / Export" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull();
  });

  it("is an item in More actions on a phone, and opens the same dialog", async () => {
    stubViewport(false);
    const user = userEvent.setup();
    renderScreen(<InventoryPage />, GRANTS);

    await screen.findByRole("button", { name: "More actions" });
    expect(screen.queryByRole("button", { name: "Import / Export" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Import / Export" }));

    expect(await screen.findByRole("dialog", { name: "Import / Export inventory items" })).toBeTruthy();
  });
});
