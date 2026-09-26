import { afterEach, describe, expect, it, vi } from "vitest";

import { buildZip } from "./zipTestKit";
import type { ZipEntry, ZipOptions } from "./zipTestKit";

/*
 * A file the size check lets through (it is a few hundred bytes) but whose directory claims it
 * unpacks to gigabytes must be refused before the spreadsheet library is loaded, let alone asked
 * to unpack it. exceljs is mocked so that merely importing it is observable.
 */

const UNPACKED_MESSAGE =
  "This file is much larger when unpacked than an import can safely read. Save it again in Excel without images or extra data, or split it into smaller files.";

let evaluated = false;

async function setUp() {
  vi.resetModules();
  evaluated = false;
  vi.doMock("exceljs", async () => {
    evaluated = true;
    return vi.importActual<object>("exceljs");
  });
  const { readWorkbook, WorkbookReadError } = await import("./workbook");
  const { makeProductEntity } = await import("./testkit");
  return { readWorkbook, WorkbookReadError, entity: makeProductEntity() };
}

function fileOfZip(entries: readonly ZipEntry[], options: ZipOptions = {}, name = "import.xlsx"): File {
  return new File([buildZip(entries, options)], name);
}

const bomb = (uncompressed: number, compressed: number): ZipEntry => ({
  name: "xl/worksheets/sheet1.xml",
  uncompressedSize: uncompressed,
  compressedSize: compressed,
});

afterEach(() => {
  vi.doUnmock("exceljs");
  vi.resetModules();
});

describe("readWorkbook refuses a hostile container before loading the spreadsheet library", () => {
  it.each([
    ["a ratio bomb: 2 GB claimed from a few kilobytes", [bomb(2_000_000_000, 6000)], {}],
    ["an entry over 25 MB", [bomb(26 * 1024 * 1024, 26 * 1024 * 1024)], {}],
    [
      "more than 40 MB in total from ordinary-looking parts",
      Array.from({ length: 5 }, (_, i) => ({ ...bomb(9 * 1024 * 1024, 9 * 1024 * 1024), name: `part${i}` })),
      {},
    ],
    ["a ZIP64 size marker", [bomb(0xffffffff, 100)], {}],
    ["a ZIP64 locator", [bomb(10, 10)], { zip64Locator: true }],
    ["more than 2000 entries", Array.from({ length: 2001 }, (_, i) => ({ name: `e${i}` })), {}],
  ] as const)("says it is too large unpacked: %s", async (_label, entries, options) => {
    const { readWorkbook, WorkbookReadError, entity } = await setUp();
    const failure = await readWorkbook(fileOfZip(entries, options), entity).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(WorkbookReadError);
    expect((failure as Error).message).toBe(UNPACKED_MESSAGE);
    expect(evaluated).toBe(false);
  });

  it.each([
    ["a truncated file", (bytes: Uint8Array) => bytes.slice(0, bytes.length - 12)],
    ["a directory that points elsewhere", () => buildZip([bomb(10, 10)], { directoryOffsetShift: 7 })],
    ["bytes that are not a zip at all", () => new TextEncoder().encode("id,name\n1,Ada\n")],
  ])("says it could not be read: %s", async (_label, make) => {
    const { readWorkbook, WorkbookReadError, entity } = await setUp();
    const file = new File([make(buildZip([bomb(10, 10)]))], "broken.xlsx");
    const failure = await readWorkbook(file, entity).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(WorkbookReadError);
    expect((failure as Error).message).toContain('"broken.xlsx" could not be read as an Excel workbook');
    expect(evaluated).toBe(false);
  });

  it("still loads the library for a sound workbook, so the check above means something", async () => {
    const { readWorkbook, entity } = await setUp();
    const actual = await vi.importActual<typeof import("exceljs")>("exceljs");
    const workbook = new actual.Workbook();
    const sheet = workbook.addWorksheet("Import");
    sheet.addRow(["SKU", "Name"]);
    sheet.addRow(["A-1", "Bracket"]);
    const bytes = await workbook.xlsx.writeBuffer();
    const parsed = await readWorkbook(new File([bytes], "ok.xlsx"), entity);
    expect(parsed.rows).toHaveLength(1);
    expect(evaluated).toBe(true);
  });

  it("checks the extension and the packed size first, without reading the file", async () => {
    const { readWorkbook, entity } = await setUp();
    await expect(readWorkbook(new File(["x"], "notes.csv"), entity)).rejects.toThrow("Save As → Excel Workbook");
    expect(evaluated).toBe(false);
  });
});
