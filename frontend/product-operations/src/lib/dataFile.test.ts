import { csvCell } from "@opsgrids/client";
import { describe, expect, it } from "vitest";

import type { ImportColumn } from "@/api/scan";
import {
  DataFileError,
  guardCell,
  normaliseHeader,
  parseCsv,
  readImportFile,
  rowsFromTable,
  sampleCsv,
  toCsv,
} from "./dataFile";

const COLUMNS: ImportColumn[] = [
  { name: "sku", required: true, description: "", example: "FAB-001" },
  { name: "quantity", required: true, description: "", example: "82.5" },
  { name: "lot_no", required: false, description: "", example: "" },
];

describe("parseCsv", () => {
  it("reads quoted fields, doubled quotes, embedded commas and newlines, CRLF and a BOM", () => {
    const text = '﻿sku,note\r\n"A,1","he said ""hi"""\r\n"B\n2",plain\r\n';
    expect(parseCsv(text)).toEqual([
      ["sku", "note"],
      ["A,1", 'he said "hi"'],
      ["B\n2", "plain"],
    ]);
  });

  it("keeps an empty trailing cell and drops fully blank lines", () => {
    expect(parseCsv("a,b,\n\nc,d,e")).toEqual([
      ["a", "b", ""],
      ["c", "d", "e"],
    ]);
  });

  it("refuses an unclosed quote", () => {
    expect(() => parseCsv('a,"b')).toThrow(DataFileError);
  });
});

describe("rowsFromTable", () => {
  it("maps headers case-, space- and star-insensitively and sends every cell as text", () => {
    const { rows, ignored, missingRequired } = rowsFromTable(
      [
        ["SKU *", "Quantity", "Lot No", "Colour"],
        ["00123", "82.5", "", "navy"],
        ["", "", "", ""],
        ["FAB-2", "10", "LOT-A", ""],
      ],
      COLUMNS,
    );
    expect(rows).toEqual([
      { sku: "00123", quantity: "82.5" }, // the leading zero survives: nothing is parsed
      { sku: "FAB-2", quantity: "10", lot_no: "LOT-A" },
    ]);
    expect(ignored).toEqual(["Colour"]);
    expect(missingRequired).toEqual([]);
  });

  it("names a missing required column", () => {
    expect(rowsFromTable([["sku"], ["A"]], COLUMNS).missingRequired).toEqual(["quantity"]);
  });

  it("normalises headers", () => {
    expect(normaliseHeader("  Lot No * ")).toBe("lot_no");
    expect(normaliseHeader("unit-cost minor")).toBe("unit_cost_minor");
  });
});

describe("readImportFile", () => {
  const csv = (name: string, body: string) => new File([body], name, { type: "text/csv" });

  it("reads a csv", async () => {
    const read = await readImportFile(csv("stock.csv", "sku,quantity\nA,1\nB,2"), COLUMNS);
    expect(read.rows).toHaveLength(2);
  });

  it("refuses other file types, empty files and over-long files before sending anything", async () => {
    await expect(readImportFile(csv("a.pdf", "x"), COLUMNS)).rejects.toThrow(/\.csv or \.xlsx/);
    await expect(readImportFile(csv("a.csv", "sku,quantity\n"), COLUMNS)).rejects.toThrow(/no rows/);
    const big = `sku,quantity\n${Array.from({ length: 5001 }, (_, i) => `S${i},1`).join("\n")}`;
    await expect(readImportFile(csv("a.csv", big), COLUMNS)).rejects.toThrow(/at most 5000/);
  });
});

describe("export files are never formulas", () => {
  it("prefixes a risky cell but leaves numbers alone", () => {
    expect(guardCell("=1+1")).toBe("'=1+1");
    expect(guardCell("+91")).toBe("'+91");
    expect(guardCell("@x")).toBe("'@x");
    expect(guardCell("-5")).toBe("-5");
    expect(guardCell("-5.250")).toBe("-5.250");
    expect(guardCell("-cmd|' /C calc'!A0")).toBe("'-cmd|' /C calc'!A0");
    expect(guardCell("plain")).toBe("plain");
  });

  it("applies the same rule as the shared client export", () => {
    expect(guardCell("-1,200")).toBe("-1,200");
    expect(guardCell("-1,20,000.50")).toBe("-1,20,000.50");
    expect(guardCell("+SUM(A1:A2)")).toBe("'+SUM(A1:A2)");
    expect(guardCell("+SUM()")).toBe("'+SUM()");
    expect(guardCell("-,")).toBe("'-,");
    expect(guardCell("-.")).toBe("'-.");
    expect(guardCell("\t=1")).toBe("'\t=1");
    expect(guardCell("\r=1")).toBe("'\r=1");
    for (const value of ["=1+1", "+SUM()", "-1,200", "@x", "\tx", "\rx", "-1.2.3", "Acme"]) {
      expect(guardCell(value)).toBe(csvCell(value).replace(/^"|"$/g, ""));
    }
  });

  it("quotes only what needs it, after guarding", () => {
    const csv = toCsv(["name", "qty"], [{ name: '=HYPERLINK("x","y")', qty: "-4" }]);
    expect(csv).toBe('name,qty\r\n"\'=HYPERLINK(""x"",""y"")",-4');
  });

  it("builds the sample sheet from the server's own columns", () => {
    expect(sampleCsv(COLUMNS)).toBe("sku,quantity,lot_no\r\nFAB-001,82.5,");
  });
});
