/**
 * What an upload field says before anything is sent: whether it takes this file, and why not.
 * The `accept` attribute only filters the picker, so this is the check a dropped file meets.
 */

import { describe, expect, it } from "vitest";

import { acceptsFile, describeAccept, formatBytes, rejectionFor } from "./files";

describe("formatBytes", () => {
  it("says a size the way a file manager does", () => {
    expect(formatBytes(1)).toBe("1 byte");
    expect(formatBytes(900)).toBe("900 bytes");
    expect(formatBytes(250_880)).toBe("245 KB");
    expect(formatBytes(1_258_291)).toBe("1.2 MB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5 MB");
  });
});

describe("describeAccept", () => {
  it("names formats as people know them", () => {
    expect(describeAccept(["image/png", "image/jpeg", "image/webp"])).toBe("PNG, JPG or WebP");
    expect(describeAccept([".pdf"])).toBe("PDF");
  });

  it("folds two spellings of one format into one name", () => {
    expect(describeAccept(["image/jpeg", ".JPG"])).toBe("JPG");
  });
});

describe("acceptsFile", () => {
  const png = { name: "Bag.PNG", type: "image/png" };

  it("matches an exact type, a family, or an extension", () => {
    expect(acceptsFile(png, ["image/png"])).toBe(true);
    expect(acceptsFile(png, ["image/*"])).toBe(true);
    expect(acceptsFile(png, [".png"])).toBe(true);
  });

  it("refuses a file matching none of them", () => {
    expect(acceptsFile({ name: "spec.pdf", type: "application/pdf" }, ["image/*"])).toBe(false);
  });

  it("takes anything when nothing is listed", () => {
    expect(acceptsFile({ name: "x.bin", type: "" }, [])).toBe(true);
  });
});

describe("rejectionFor", () => {
  const rules = { accept: ["image/png", "image/jpeg"], maxSize: 5 * 1024 * 1024 };

  it("says the file is the wrong kind, naming the kinds that are right", () => {
    expect(rejectionFor({ name: "a.gif", type: "image/gif", size: 10 }, rules)).toBe("a.gif isn't a PNG or JPG file.");
  });

  it("says the file is too large, with both sizes", () => {
    expect(rejectionFor({ name: "b.png", type: "image/png", size: 7 * 1024 * 1024 }, rules)).toBe(
      "b.png is 7 MB; the limit is 5 MB.",
    );
  });

  it("has nothing to say about a file it takes", () => {
    expect(rejectionFor({ name: "c.jpg", type: "image/jpeg", size: 1024 }, rules)).toBeNull();
  });
});
