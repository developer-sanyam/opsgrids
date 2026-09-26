import { describe, expect, it } from "vitest";

import { fileWithRows, makeProductEntity } from "./testkit";
import { readBytes } from "./excel";
import {
  MAX_ENTRIES,
  MAX_ENTRY_BYTES,
  MAX_RATIO,
  MAX_UNPACKED_BYTES,
  RATIO_CHECK_ABOVE_BYTES,
  inspectArchive,
} from "./zipGuard";
import { buildZip } from "./zipTestKit";
import type { ZipEntry } from "./zipTestKit";

const MB = 1024 * 1024;

/** An entry that claims `uncompressed` bytes packed into `compressed`, with no data behind it. */
function claim(uncompressed: number, compressed = uncompressed, name = "xl/worksheets/sheet1.xml"): ZipEntry {
  return { name, uncompressedSize: uncompressed, compressedSize: compressed };
}

describe("inspectArchive: a workbook", () => {
  it("passes what Excel-shaped writers produce", async () => {
    const file = await fileWithRows(["SKU", "Name"], [["A-1", "Bracket"], ["A-2", "Nut"]]);
    expect(inspectArchive(await readBytes(file))).toBeNull();
  });

  it("passes the sample sheet at its heaviest: many columns over the whole formatted range", async () => {
    const columns = Array.from({ length: 55 }, (_, i) => ({
      key: `c${i}`,
      header: `Column ${i}`,
      kind: "text" as const,
      help: "A column.",
    }));
    const entity = makeProductEntity({ columns, sampleRows: [{ c0: "a" }] });
    const { buildSampleWorkbook } = await import("./template");
    const blob = await buildSampleWorkbook(entity, {});
    expect(inspectArchive(await readBytes(blob))).toBeNull();
  });
});

describe("inspectArchive: sizes the directory declares", () => {
  it("passes a small archive and an empty one", () => {
    expect(inspectArchive(buildZip([claim(2000, 800)]))).toBeNull();
    expect(inspectArchive(buildZip([]))).toBeNull();
  });

  it("refuses more than 40 MB unpacked in total, and passes exactly 40 MB", () => {
    const half = MAX_UNPACKED_BYTES / 2;
    expect(inspectArchive(buildZip([claim(half), claim(half, half, "b")]))).toBeNull();
    expect(inspectArchive(buildZip([claim(half), claim(half + 1, half + 1, "b")]))).toBe("too-large");
  });

  it("adds up many entries that are each small", () => {
    const each = Math.floor(MAX_UNPACKED_BYTES / 20) + 1;
    const entries = Array.from({ length: 20 }, (_, i) => claim(each, each, `part${i}`));
    expect(inspectArchive(buildZip(entries))).toBe("too-large");
  });

  it("refuses a single entry over 25 MB, and passes exactly 25 MB", () => {
    expect(inspectArchive(buildZip([claim(MAX_ENTRY_BYTES)]))).toBeNull();
    expect(inspectArchive(buildZip([claim(MAX_ENTRY_BYTES + 1)]))).toBe("too-large");
  });

  it("refuses an entry over 1 MB that unpacks more than 100 to 1, and passes 100 to 1 exactly", () => {
    // A few kilobytes of repeated cells inflating to gigabytes is the shape of a bomb.
    expect(inspectArchive(buildZip([claim(2 * MB, 10_000)]))).toBe("too-large");
    expect(inspectArchive(buildZip([claim(24 * MB, 3_000)]))).toBe("too-large");
    expect(inspectArchive(buildZip([claim(2_000_000, 20_000)]))).toBeNull();
    expect(inspectArchive(buildZip([claim(2_000_000, 19_999)]))).toBe("too-large");
    expect(MAX_RATIO).toBe(100);
  });

  it("treats an entry declared to pack into nothing as an infinite ratio", () => {
    expect(inspectArchive(buildZip([claim(2 * MB, 0)]))).toBe("too-large");
  });

  it("does not hold an entry of 1 MB or less to the ratio: it cannot hurt", () => {
    expect(inspectArchive(buildZip([claim(RATIO_CHECK_ABOVE_BYTES, 1)]))).toBeNull();
    expect(inspectArchive(buildZip([claim(RATIO_CHECK_ABOVE_BYTES + 1, 1)]))).toBe("too-large");
  });

  it("refuses more than 2000 entries, and passes exactly 2000", () => {
    const entries = (count: number) => Array.from({ length: count }, (_, i) => claim(10, 10, `e${i}`));
    expect(inspectArchive(buildZip(entries(MAX_ENTRIES)))).toBeNull();
    expect(inspectArchive(buildZip(entries(MAX_ENTRIES + 1)))).toBe("too-large");
  });
});

describe("inspectArchive: ZIP64", () => {
  it("refuses a 32-bit size of 0xFFFFFFFF, which says the real size is elsewhere", () => {
    expect(inspectArchive(buildZip([claim(0xffffffff, 100)]))).toBe("too-large");
    expect(inspectArchive(buildZip([claim(100, 0xffffffff)]))).toBe("too-large");
  });

  it("refuses a local header offset of 0xFFFFFFFF", () => {
    expect(inspectArchive(buildZip([claim(10)], { localOffset: 0xffffffff }))).toBe("too-large");
  });

  it("refuses a ZIP64 end-of-central-directory locator", () => {
    expect(inspectArchive(buildZip([claim(10)], { zip64Locator: true }))).toBe("too-large");
  });

  it("refuses an entry count of 0xFFFF in the end record", () => {
    expect(inspectArchive(buildZip([claim(10)], { declaredEntries: 0xffff }))).toBe("too-large");
    expect(inspectArchive(buildZip([claim(10)], { entriesOnDisk: 0xffff }))).toBe("too-large");
  });

  it("refuses a directory size or offset of 0xFFFFFFFF in the end record", () => {
    expect(inspectArchive(buildZip([claim(10)], { directorySize: 0xffffffff }))).toBe("too-large");
    expect(inspectArchive(buildZip([claim(10)], { directoryOffset: 0xffffffff }))).toBe("too-large");
  });
});

describe("inspectArchive: a container that is not sound", () => {
  const sound = buildZip([claim(10), claim(20, 20, "b")]);

  it("refuses anything too short to hold an end record, and bytes that are not a zip", () => {
    expect(inspectArchive(new Uint8Array(0))).toBe("malformed");
    expect(inspectArchive(new Uint8Array(10))).toBe("malformed");
    expect(inspectArchive(new TextEncoder().encode("this is a csv, not a workbook, not even close"))).toBe("malformed");
    expect(inspectArchive(new Uint8Array(70_000).fill(7))).toBe("malformed");
  });

  it("refuses a file cut short: no end record, or a directory that runs off the end", () => {
    expect(inspectArchive(sound.slice(0, sound.length - 10))).toBe("malformed");
    expect(inspectArchive(sound.slice(0, sound.length - 22))).toBe("malformed");
    expect(inspectArchive(sound.slice(0, 60))).toBe("malformed");
  });

  it("refuses an end record that points somewhere the directory is not", () => {
    expect(inspectArchive(buildZip([claim(10)], { directoryOffsetShift: 5 }))).toBe("malformed");
    expect(inspectArchive(buildZip([claim(10)], { directoryOffsetShift: -5 }))).toBe("malformed");
    expect(inspectArchive(buildZip([claim(10)], { directorySizeShift: 3 }))).toBe("malformed");
    expect(inspectArchive(buildZip([claim(10)], { directorySizeShift: -3 }))).toBe("malformed");
  });

  it("refuses a directory with data between it and the end record", () => {
    expect(inspectArchive(buildZip([claim(10)], { gap: new Uint8Array(8) }))).toBe("malformed");
  });

  it("refuses an entry count that is not the number of entries the directory holds", () => {
    expect(inspectArchive(buildZip([claim(10)], { declaredEntries: 2 }))).toBe("malformed");
    expect(inspectArchive(buildZip([claim(10), claim(10, 10, "b")], { declaredEntries: 1 }))).toBe("malformed");
  });

  it("refuses counts that disagree with each other, and a multi-disk archive", () => {
    expect(inspectArchive(buildZip([claim(10)], { entriesOnDisk: 2 }))).toBe("malformed");
    expect(inspectArchive(buildZip([claim(10)], { disk: 1 }))).toBe("malformed");
    expect(inspectArchive(buildZip([claim(10)], { directoryDisk: 1 }))).toBe("malformed");
  });

  it("refuses a directory entry whose signature is wrong", () => {
    const broken = sound.slice();
    const view = new DataView(broken.buffer);
    // The first central directory header starts right after the local parts.
    const first = 30 + 24 + 0 + 30 + "b".length;
    expect(view.getUint32(first, true)).toBe(0x02014b50);
    view.setUint32(first, 0x12345678, true);
    expect(inspectArchive(broken)).toBe("malformed");
  });

  it("reads the last end record in the file, the one the unpacking library uses", () => {
    // A second end record smuggled into the comment: whoever reads the file finds this one first.
    const fake = buildZip([]);
    expect(inspectArchive(buildZip([claim(10)], { comment: fake }))).toBe("malformed");
  });
});
