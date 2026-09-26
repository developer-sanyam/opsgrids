import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * exceljs is about 250 KB gzip. It must load only when someone opens the dialog, so importing
 * the package (which every app does at startup) must never evaluate it.
 */
describe("exceljs is loaded on demand", () => {
  afterEach(() => {
    vi.doUnmock("exceljs");
    vi.resetModules();
  });

  it("is not evaluated by importing the client package, and is by using the engine", async () => {
    vi.resetModules();
    let evaluated = false;
    vi.doMock("exceljs", () => {
      evaluated = true;
      return { Workbook: class Workbook {} };
    });

    await import("../index");
    expect(evaluated).toBe(false);

    const { loadExcel } = await import("./excel");
    await loadExcel();
    expect(evaluated).toBe(true);
  });

  it("accepts the library whether the runtime exposes it on the namespace or on default", async () => {
    vi.resetModules();
    vi.doMock("exceljs", () => ({ default: { Workbook: class Workbook {} } }));
    const { loadExcel } = await import("./excel");
    const excel = await loadExcel();
    expect(typeof excel.Workbook).toBe("function");
  });
});
