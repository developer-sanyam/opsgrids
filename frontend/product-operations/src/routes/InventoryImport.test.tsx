/**
 * `/inventory/import`: the file is read in the browser into text cells, previewed on the server,
 * and committed only when the preview is clean — all or nothing. A refused commit, a duplicate
 * key retry, a missing permission and an export are all covered against a mocked API.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ImportResult, ImportTemplate } from "@/api/scan";
import { ApiError } from "@opsgrids/client";
import { renderScreen } from "@/test-utils/renderScreen";

const getImportTemplate = vi.fn();
const runImport = vi.fn();
const exportData = vi.fn();
const downloadText = vi.fn();

vi.mock("@/api/scan", () => ({
  getImportTemplate: (...a: unknown[]) => getImportTemplate(...a),
  runImport: (...a: unknown[]) => runImport(...a),
  exportData: (...a: unknown[]) => exportData(...a),
}));
vi.mock("@/lib/dataFile", async () => {
  const actual = await vi.importActual<typeof import("@/lib/dataFile")>("@/lib/dataFile");
  return { ...actual, downloadText: (...a: unknown[]) => downloadText(...a) };
});

import { InventoryImportPage } from "./InventoryImport";

const TEMPLATE: ImportTemplate = {
  entity: "items",
  max_rows: 5000,
  columns: [
    { name: "sku", required: true, description: "Unique SKU", example: "FAB-001" },
    { name: "name", required: true, description: "Name", example: "Cotton" },
  ],
};

const ALL = [
  "operations.inventory.data.import:ORG",
  "operations.inventory.data.export:ORG",
  "operations.inventory.item.write:ORG",
  "operations.inventory.location.write:ORG",
  "operations.inventory.lot.write:ORG",
  "operations.inventory.roll.write:ORG",
  "operations.inventory.stock.adjust:ORG",
];

function result(over: Partial<ImportResult>): ImportResult {
  return {
    entity: "items",
    mode: "preview",
    committed: false,
    replayed: false,
    run_id: null,
    total: 2,
    created: 2,
    skipped: 0,
    error_count: 0,
    errors: [],
    rows: [],
    ...over,
  };
}

const csv = (body: string) => new File([body], "items.csv", { type: "text/csv" });

async function upload(body: string) {
  const user = userEvent.setup();
  await user.upload(await screen.findByLabelText("Choose a CSV or Excel file"), csv(body));
  return user;
}

beforeEach(() => {
  getImportTemplate.mockResolvedValue(TEMPLATE);
});

describe("preview", () => {
  it("sends the file's cells as text and reports a clean file as ready, saving nothing", async () => {
    runImport.mockResolvedValue(result({}));
    renderScreen(<InventoryImportPage />, ALL);
    await upload("SKU *,Name,Colour\r\n00123,Cotton,navy\r\nFAB-2,Dye,red\r\n");
    expect(await screen.findByText("2 rows are ready")).toBeTruthy();
    expect(runImport).toHaveBeenCalledTimes(1);
    expect(runImport.mock.calls[0]?.[0]).toMatchObject({
      entity: "items",
      mode: "preview",
      onDuplicate: "fail",
      rows: [
        { sku: "00123", name: "Cotton" },
        { sku: "FAB-2", name: "Dye" },
      ],
    });
    expect(runImport.mock.calls[0]?.[1]).toBeUndefined(); // a preview needs no key
    expect(screen.getByText(/Colour is not an import column/)).toBeTruthy();
    expect(screen.getByText(/Nothing has been saved yet/)).toBeTruthy();
  });

  it("lists every problem by row and column and will not let the file be imported", async () => {
    runImport.mockResolvedValue(
      result({
        created: 0,
        error_count: 2,
        errors: [
          { row: 2, field: "warehouse", code: "unknown_warehouse", message: "No warehouse named 'X'" },
          { row: 7, field: "sku", code: "already_exists", message: "An item with SKU 'A' already exists" },
        ],
      }),
    );
    renderScreen(<InventoryImportPage />, ALL);
    await upload("sku,name\nA,x\nB,y");
    expect(await screen.findByText("2 rows have problems")).toBeTruthy();
    expect(screen.getByText("No warehouse named 'X'")).toBeTruthy();
    expect(screen.getByText("warehouse")).toBeTruthy();
    const button = screen.getByRole("button", { name: /^Import \d+ rows?$/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("names a missing required column before anything is sent", async () => {
    renderScreen(<InventoryImportPage />, ALL);
    await upload("sku\nA");
    expect(await screen.findByText(/no name column/)).toBeTruthy();
    expect(runImport).not.toHaveBeenCalled();
  });

  it("refuses a file that is not csv or xlsx", async () => {
    renderScreen(<InventoryImportPage />, ALL);
    const user = userEvent.setup({ applyAccept: false });
    await user.upload(
      await screen.findByLabelText("Choose a CSV or Excel file"),
      new File(["x"], "items.pdf", { type: "application/pdf" }),
      // The input's accept filter is advisory; the reader still refuses.
    );
    expect(await screen.findByText(/Import a \.csv or \.xlsx file/)).toBeTruthy();
    expect(runImport).not.toHaveBeenCalled();
  });
});

describe("commit", () => {
  it("commits a clean preview once, with an idempotency key, and says every row was saved", async () => {
    runImport
      .mockResolvedValueOnce(result({}))
      .mockResolvedValueOnce(result({ mode: "commit", committed: true, run_id: "run-1" }));
    renderScreen(<InventoryImportPage />, ALL);
    const user = await upload("sku,name\nA,x\nB,y");
    await user.click(await screen.findByRole("button", { name: "Import 2 rows" }));
    expect(await screen.findByText("Imported 2 rows")).toBeTruthy();
    const [request, key] = runImport.mock.calls[1] as [Record<string, unknown>, string];
    expect(request).toMatchObject({ mode: "commit", entity: "items", sourceName: "items.csv" });
    expect(key).toMatch(/[0-9a-f-]{36}/);
    expect(screen.getByText(/Every row was saved together/)).toBeTruthy();
  });

  it("shows every reason when the server refuses a commit the preview allowed, and keeps the key", async () => {
    const refused = new ApiError(422, "Nothing was imported: 1 row had problems.", {
      errors: [{ row: 2, field: "sku", code: "already_exists", message: "SKU 'B' already exists" }],
    } as never);
    runImport
      .mockResolvedValueOnce(result({}))
      .mockRejectedValueOnce(refused)
      .mockRejectedValueOnce(refused);
    renderScreen(<InventoryImportPage />, ALL);
    const user = await upload("sku,name\nA,x\nB,y");
    await user.click(await screen.findByRole("button", { name: "Import 2 rows" }));
    expect(await screen.findByText("Nothing was imported")).toBeTruthy();
    expect(screen.getByText("SKU 'B' already exists")).toBeTruthy();
    // Retrying the same file sends the same key (a replay, never a second import).
    await user.click(screen.getByRole("button", { name: "Import 2 rows" }));
    await waitFor(() => expect(runImport).toHaveBeenCalledTimes(3));
    expect(runImport.mock.calls[1]?.[1]).toBe(runImport.mock.calls[2]?.[1]);
  });

  it("re-checks the file when the duplicate policy changes", async () => {
    runImport.mockResolvedValue(result({}));
    renderScreen(<InventoryImportPage />, ALL);
    const user = await upload("sku,name\nA,x");
    await screen.findByText("2 rows are ready");
    await user.selectOptions(
      screen.getByLabelText("A record that already exists"),
      "Leave it alone and import the rest",
    );
    await waitFor(() => expect(runImport).toHaveBeenCalledTimes(2));
    expect(runImport.mock.calls[1]?.[0]).toMatchObject({ onDuplicate: "skip", mode: "preview" });
  });
});

describe("permissions", () => {
  it("offers only the entities the caller may create, plus export if granted", async () => {
    renderScreen(<InventoryImportPage />, [
      "operations.inventory.data.import:ORG",
      "operations.inventory.lot.write:ORG",
    ]);
    const select = (await screen.findByLabelText("What are you importing?")) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["lots"]);
    expect(screen.queryByText("Export")).toBeNull();
  });

  it("shows no import at all without the dangerous permission", async () => {
    renderScreen(<InventoryImportPage />, [
      "operations.inventory.item.write:ORG",
      "operations.inventory.data.export:ORG",
    ]);
    expect(await screen.findByText("Importing needs permission")).toBeTruthy();
    expect(screen.queryByLabelText("Choose a CSV or Excel file")).toBeNull();
    expect(getImportTemplate).not.toHaveBeenCalled();
    expect(screen.getByText("Export")).toBeTruthy();
  });
});

describe("export", () => {
  it("downloads a CSV of the list and reports how many rows it holds", async () => {
    exportData.mockResolvedValue({
      entity: "rolls",
      columns: ["sku", "roll_no"],
      rows: [{ sku: "COT", roll_no: "R-1" }],
      truncated: false,
    });
    renderScreen(<InventoryImportPage />, ALL);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Rolls" }));
    await waitFor(() => expect(downloadText).toHaveBeenCalledTimes(1));
    expect(downloadText.mock.calls[0]).toEqual(["inventory-rolls.csv", "sku,roll_no\r\nCOT,R-1"]);
    expect(await screen.findByText("Rolls: 1 row downloaded.")).toBeTruthy();
  });

  it("says when the list was longer than one export holds", async () => {
    exportData.mockResolvedValue({ entity: "ledger", columns: ["id"], rows: [{ id: "1" }], truncated: true });
    renderScreen(<InventoryImportPage />, ALL);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Stock ledger" }));
    expect(await screen.findByText(/longer than one export holds/)).toBeTruthy();
  });

  it("explains a refused export", async () => {
    exportData.mockRejectedValue(new ApiError(403, "Missing permission: operations.inventory.stock.read", null));
    renderScreen(<InventoryImportPage />, ALL);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Lots" }));
    expect(await screen.findByText(/Missing permission/)).toBeTruthy();
    expect(downloadText).not.toHaveBeenCalled();
  });
});
