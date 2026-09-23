import { describe, expect, it } from "vitest";

import { parseContactCsv, parseContactCsvRows } from "./csv";

describe("parseContactCsv", () => {
  it("reads email, name pairs without a header", () => {
    expect(parseContactCsv("priya@example.com,Priya Raghavan\narjun@example.com,Arjun Mehta")).toEqual([
      { email: "priya@example.com", name: "Priya Raghavan" },
      { email: "arjun@example.com", name: "Arjun Mehta" },
    ]);
  });

  it("skips a header row starting with email, case-insensitively", () => {
    expect(parseContactCsv("Email,Name\npriya@example.com,Priya Raghavan")).toEqual([
      { email: "priya@example.com", name: "Priya Raghavan" },
    ]);
  });

  it("drops blank lines between rows", () => {
    expect(parseContactCsv("priya@example.com,Priya\n\n\narjun@example.com,Arjun")).toEqual([
      { email: "priya@example.com", name: "Priya" },
      { email: "arjun@example.com", name: "Arjun" },
    ]);
  });

  it("defaults a missing name to an empty string", () => {
    expect(parseContactCsv("priya@example.com")).toEqual([{ email: "priya@example.com", name: "" }]);
  });

  it("unescapes doubled quotes inside a quoted field", () => {
    expect(parseContactCsv('priya@example.com,"Priya ""P.R."" Raghavan"')).toEqual([
      { email: "priya@example.com", name: 'Priya "P.R." Raghavan' },
    ]);
  });

  it("keeps a comma inside a quoted field out of the column split", () => {
    expect(parseContactCsv('priya@example.com,"Raghavan, Priya"')).toEqual([
      { email: "priya@example.com", name: "Raghavan, Priya" },
    ]);
  });

  it("reads a newline embedded in a quoted field as part of the cell, not a new row", () => {
    // A spreadsheet cell with a line break, pasted as CSV, quotes the whole field — the newline
    // inside those quotes is data, and splitting on every "\n" first would cut this row in two.
    const text = 'priya@example.com,"Priya\nRaghavan"\narjun@example.com,Arjun';
    expect(parseContactCsv(text)).toEqual([
      { email: "priya@example.com", name: "Priya\nRaghavan" },
      { email: "arjun@example.com", name: "Arjun" },
    ]);
  });
});

describe("parseContactCsvRows", () => {
  it("reports the original pasted line, accounting for the skipped header", () => {
    const text = "email,name\npriya@example.com,Priya\narjun@example.com,Arjun";
    expect(parseContactCsvRows(text)).toEqual([
      { email: "priya@example.com", name: "Priya", line: 2 },
      { email: "arjun@example.com", name: "Arjun", line: 3 },
    ]);
  });

  it("reports the original pasted line, accounting for blank lines skipped in between", () => {
    const text = "priya@example.com,Priya\n\narjun@example.com,Arjun";
    expect(parseContactCsvRows(text)).toEqual([
      { email: "priya@example.com", name: "Priya", line: 1 },
      { email: "arjun@example.com", name: "Arjun", line: 3 },
    ]);
  });

  it("reports the line a row started on even when one of its own fields spans several lines", () => {
    const text = 'email,name\n"priya@example.com","Priya\nRaghavan"\narjun@example.com,Arjun';
    expect(parseContactCsvRows(text)).toEqual([
      { email: "priya@example.com", name: "Priya\nRaghavan", line: 2 },
      { email: "arjun@example.com", name: "Arjun", line: 4 },
    ]);
  });
});
