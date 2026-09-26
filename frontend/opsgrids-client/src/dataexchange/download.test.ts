import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { downloadBlob } from "./download";

describe("downloadBlob", () => {
  const blob = new Blob(["data"], { type: "text/plain" });
  let clicked: { href: string; download: string; attached: boolean }[];

  beforeEach(() => {
    vi.useFakeTimers();
    clicked = [];
    URL.createObjectURL = vi.fn(() => "blob:test/1");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click(this: HTMLAnchorElement) {
      clicked.push({ href: this.href, download: this.download, attached: document.body.contains(this) });
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("clicks a link to the object URL that carries the file name", () => {
    downloadBlob("companies-2026-09-26.xlsx", blob);
    expect(URL.createObjectURL).toHaveBeenCalledWith(blob);
    expect(clicked).toEqual([{ href: "blob:test/1", download: "companies-2026-09-26.xlsx", attached: true }]);
  });

  it("leaves nothing in the page afterwards", () => {
    downloadBlob("a.xlsx", blob);
    expect(document.querySelector("a[download]")).toBeNull();
  });

  it("lets go of the object URL, but not in the same tick as the click", () => {
    downloadBlob("a.xlsx", blob);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test/1");
  });
});
