import { describe, expect, it } from "vitest";

import {
  normalisePostalCode,
  parseCsvRows,
  parseImportCsv,
  parsePostalCodeText,
  planImport,
  planZoneSave,
} from "./postalCodes";
import type { PostalCodeMap } from "./postalCodes";

/**
 * The pure core, where the real bugs are.
 *
 * Everything here is a case a merchant produces without trying: a column pasted out of Excel with
 * stray spaces, the same code typed twice, a code that already belongs to another zone, a CSV with
 * a header row, and a CSV with a trailing comma. Each of these, got wrong, either loses data
 * silently or moves a postal code between zones without saying so — and ADR-0010 §2 makes that the
 * one thing this module must never do.
 */

const ZONES = [
  { id: "z-central", code: "MUM-CENTRAL", name: "Mumbai — central" },
  { id: "z-suburbs", code: "MUM-SUB", name: "Mumbai — suburbs" },
];

function mapOf(entries: Record<string, string>): PostalCodeMap {
  const map = new Map<string, { zoneId: string; zoneCode: string; zoneName: string }>();
  for (const [code, zoneId] of Object.entries(entries)) {
    const zone = ZONES.find((candidate) => candidate.id === zoneId);
    if (!zone) throw new Error(`test fixture names an unknown zone: ${zoneId}`);
    map.set(code, { zoneId: zone.id, zoneCode: zone.code, zoneName: zone.name });
  }
  return map;
}

describe("normalisePostalCode", () => {
  it("strips whitespace, punctuation and case", () => {
    expect(normalisePostalCode(" 400 050 ")).toBe("400050");
    expect(normalisePostalCode("400-050")).toBe("400050");
    expect(normalisePostalCode("\t400050\n")).toBe("400050");
    expect(normalisePostalCode("sw1a 1aa")).toBe("SW1A1AA");
  });
});

describe("parsePostalCodeText", () => {
  it("reads one per line, comma-separated and tab-separated in the same block", () => {
    const parsed = parsePostalCodeText("400050\n400051, 400052\t400053");
    expect(parsed.codes.map((code) => code.value)).toEqual([
      "400050",
      "400051",
      "400052",
      "400053",
    ]);
    expect(parsed.rejected).toHaveLength(0);
  });

  it("tells a spaced PIN apart from two PINs separated by a space", () => {
    // `400 050` is one code written the way it is printed; `400050 400051` is two.
    expect(parsePostalCodeText("400 050").codes.map((code) => code.value)).toEqual(["400050"]);
    expect(parsePostalCodeText("400050 400051").codes.map((code) => code.value)).toEqual([
      "400050",
      "400051",
    ]);
  });

  it("drops blank lines and stray separators without calling them errors", () => {
    const parsed = parsePostalCodeText("400050,\n\n  \n,400051,,");
    expect(parsed.codes.map((code) => code.value)).toEqual(["400050", "400051"]);
    expect(parsed.rejected).toHaveLength(0);
  });

  it("rejects anything that is not a six-digit Indian PIN, naming the line", () => {
    const parsed = parsePostalCodeText("400050\n40005\nABCDEF\n045123");
    expect(parsed.codes.map((code) => code.value)).toEqual(["400050"]);
    expect(parsed.rejected.map((entry) => [entry.line, entry.value])).toEqual([
      [2, "40005"],
      [3, "ABCDEF"],
      [4, "045123"],
    ]);
  });
});

describe("planZoneSave", () => {
  const map = mapOf({
    "400050": "z-central",
    "400051": "z-central",
    "400072": "z-suburbs",
    "400059": "z-suburbs",
  });

  it("separates new codes, codes already here, and codes moving out of another zone", () => {
    const plan = planZoneSave("400050\n400072\n400090", "z-central", map);

    expect(plan.unchanged).toEqual(["400050"]);
    expect(plan.added).toEqual(["400090"]);
    expect(plan.moves).toEqual([
      {
        value: "400072",
        from: { zoneId: "z-suburbs", zoneCode: "MUM-SUB", zoneName: "Mumbai — suburbs" },
      },
    ]);
  });

  it("counts what a save would remove, because PUT carries the whole set", () => {
    const plan = planZoneSave("400050", "z-central", map);
    expect(plan.removed).toEqual(["400051"]);
  });

  it("collapses a code typed twice in one paste and says which lines", () => {
    const plan = planZoneSave("400090\n400090\n 400-090 ", "z-central", map);

    expect(plan.save).toEqual(["400090"]);
    expect(plan.added).toEqual(["400090"]);
    expect(plan.duplicates).toEqual([{ value: "400090", lines: [1, 2, 3] }]);
  });

  it("normalises before comparing, so a spaced paste is not a false 'new'", () => {
    const plan = planZoneSave("400 050\n400-051", "z-central", map);
    expect(plan.added).toEqual([]);
    expect(plan.unchanged).toEqual(["400050", "400051"]);
    expect(plan.removed).toEqual([]);
  });

  it("refuses invalid codes instead of sending them", () => {
    const plan = planZoneSave("400050\n99\n400090", "z-central", map);
    expect(plan.save).toEqual(["400050", "400090"]);
    expect(plan.rejected).toHaveLength(1);
    expect(plan.rejected[0]?.reason).toBe("not-indian-pin");
  });
});

describe("parseCsvRows", () => {
  it("handles CRLF, quoted commas and doubled quotes", () => {
    const rows = parseCsvRows('400050,"Andheri East, Mumbai"\r\n400051,"He said ""yes"""\r\n');
    expect(rows).toHaveLength(2);
    expect(rows[0]?.cells).toEqual(["400050", "Andheri East, Mumbai"]);
    expect(rows[1]?.cells).toEqual(["400051", 'He said "yes"']);
  });
});

describe("parseImportCsv", () => {
  it("recognises and skips a header row, whichever order the columns are in", () => {
    const parsed = parseImportCsv("zone_code,postal_code\nMUM-SUB,400072\n");
    expect(parsed.headerLine).toBe(1);
    expect(parsed.rows).toEqual([
      { postalRaw: "400072", postalCode: "400072", zoneCode: "MUM-SUB", line: 2 },
    ]);
  });

  it("takes the first two values when there is no header", () => {
    const parsed = parseImportCsv("400050,MUM-CENTRAL\n400072,MUM-SUB\n");
    expect(parsed.headerLine).toBeNull();
    expect(parsed.rows.map((row) => row.zoneCode)).toEqual(["MUM-CENTRAL", "MUM-SUB"]);
  });

  it("ignores a stray empty column, leading or trailing", () => {
    const parsed = parseImportCsv("400050,MUM-CENTRAL,\n400072,,MUM-SUB\n,400059,MUM-SUB\n");
    expect(parsed.malformed).toEqual([]);
    expect(parsed.rows.map((row) => [row.postalCode, row.zoneCode])).toEqual([
      ["400050", "MUM-CENTRAL"],
      ["400072", "MUM-SUB"],
      ["400059", "MUM-SUB"],
    ]);
  });

  it("reports a row that is missing one of its two values rather than dropping it", () => {
    const parsed = parseImportCsv("400050,MUM-CENTRAL\n400072\n");
    expect(parsed.rows).toHaveLength(1);
    expect(parsed.malformed).toEqual([{ line: 2, detail: "No zone code in this row" }]);
  });
});

describe("planImport", () => {
  const map = mapOf({ "400050": "z-central", "400072": "z-suburbs" });

  it("blocks a file that names the same postal code twice, with the line numbers", () => {
    const plan = planImport(
      "postal_code,zone_code\n400090,MUM-CENTRAL\n400090,MUM-SUB\n",
      "merge",
      ZONES,
      map,
    );

    expect(plan.blocked).toBe(true);
    expect(plan.duplicates).toEqual([{ value: "400090", lines: [2, 3] }]);
  });

  it("blocks a zone code that names nothing, with the line numbers", () => {
    const plan = planImport("400090,NOWHERE\n", "merge", ZONES, map);

    expect(plan.blocked).toBe(true);
    expect(plan.unknownZones).toEqual([{ code: "NOWHERE", lines: [1] }]);
  });

  it("names the zone each code is leaving", () => {
    const plan = planImport("400072,MUM-CENTRAL\n", "merge", ZONES, map);

    expect(plan.blocked).toBe(false);
    expect(plan.moves).toEqual([
      {
        value: "400072",
        from: { zoneId: "z-suburbs", zoneCode: "MUM-SUB", zoneName: "Mumbai — suburbs" },
      },
    ]);
    expect(plan.perZone).toEqual([
      { zoneId: "z-central", zoneCode: "MUM-CENTRAL", zoneName: "Mumbai — central", count: 1 },
    ]);
  });

  it("removes nothing on merge and everything unmentioned on replace", () => {
    const merge = planImport("400050,MUM-CENTRAL\n", "merge", ZONES, map);
    expect(merge.removed).toEqual([]);

    const replace = planImport("400050,MUM-CENTRAL\n", "replace", ZONES, map);
    expect(replace.removed).toEqual(["400072"]);
  });

  it("counts every row it read, including the ones it refused", () => {
    const plan = planImport(
      "postal_code,zone_code\n400090,MUM-CENTRAL\n99,MUM-CENTRAL\n400091\n",
      "merge",
      ZONES,
      map,
    );

    expect(plan.rowsRead).toBe(3);
    expect(plan.rejected).toHaveLength(1);
    expect(plan.malformed).toHaveLength(1);
  });
});
