import { afterEach, describe, expect, it, vi } from "vitest";

import { downloadCsv, downloadCsvFile, downloadCsvGrid, toCsv, toCsvGrid } from "./csvExport";

interface Person {
  name: string;
  note: string;
}

const COLUMNS = [
  { header: "Name", value: (p: Person) => p.name },
  { header: "Note", value: (p: Person) => p.note },
];

describe("toCsv", () => {
  it("writes a header row and one CRLF-ended line per row", () => {
    const rows: Person[] = [
      { name: "Asha", note: "ok" },
      { name: "Ben", note: "fine" },
    ];
    expect(toCsv(rows, COLUMNS)).toBe("Name,Note\r\nAsha,ok\r\nBen,fine");
  });

  it("is the header alone for no rows", () => {
    expect(toCsv([], COLUMNS)).toBe("Name,Note");
  });

  it("quotes commas, quotes and line breaks", () => {
    expect(toCsv([{ name: 'A "B", C', note: "x\ny" }], COLUMNS)).toBe('Name,Note\r\n"A ""B"", C","x\ny"');
  });

  it("guards a cell that would run as a formula, so exported text cannot execute", () => {
    expect(toCsv([{ name: "=HYPERLINK(\"http://x\")", note: "-12.50" }], COLUMNS)).toBe(
      "Name,Note\r\n\"'=HYPERLINK(\"\"http://x\"\")\",-12.50",
    );
  });
});

describe("toCsvGrid", () => {
  it("writes string rows under a header with the same quoting and line ends", () => {
    expect(toCsvGrid(["A", "B"], [["1", "2,3"], ["@x", "y"]])).toBe("A,B\r\n1,\"2,3\"\r\n'@x,y");
  });
});

function readBlob(blob: Blob | undefined): Promise<string> {
  return new Promise((resolve, reject) => {
    if (blob === undefined) return reject(new Error("no blob"));
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

describe("downloading", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function stubBrowser() {
    const create = vi.fn(() => "blob:csv");
    const revoke = vi.fn();
    vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    return { create, revoke, click };
  }

  it("clicks a download link named for the file and cleans up after itself", () => {
    const { create, revoke, click } = stubBrowser();
    downloadCsvFile("people.csv", "a,b");
    expect(create).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith("blob:csv");
    expect(document.querySelector("a[download]")).toBeNull();
  });

  it("builds the CSV from columns and from a grid before it downloads", async () => {
    const { create } = stubBrowser();
    downloadCsv("p.csv", [{ name: "Asha", note: "ok" }], COLUMNS);
    downloadCsvGrid("g.csv", ["A"], [["1"]]);
    const blobs = create.mock.calls.map((call) => (call as unknown[])[0] as Blob);
    expect(await readBlob(blobs[0])).toBe("Name,Note\r\nAsha,ok");
    expect(await readBlob(blobs[1])).toBe("A\r\n1");
  });
});
