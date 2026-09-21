import { describe, expect, it } from "vitest";

import { addDays, addWeeks, isoOf, mondayOf, weekFrom } from "./dates";

describe("isoOf", () => {
  it("formats a UTC date as YYYY-MM-DD", () => {
    expect(isoOf(new Date("2026-09-21T10:00:00Z"))).toBe("2026-09-21");
  });
});

describe("mondayOf", () => {
  it("returns the same date when it's already a Monday", () => {
    expect(mondayOf("2026-09-21")).toBe("2026-09-21");
  });

  it("walks a Sunday back to the Monday that started its week", () => {
    expect(mondayOf("2026-09-27")).toBe("2026-09-21");
  });

  it("crosses a month boundary when the week starts in the prior month", () => {
    // 2026-10-01 is a Thursday; its week starts the Monday before, in September.
    expect(mondayOf("2026-10-01")).toBe("2026-09-28");
  });
});

describe("weekFrom", () => {
  it("returns the seven consecutive ISO dates starting Monday", () => {
    expect(weekFrom("2026-09-21")).toEqual([
      "2026-09-21",
      "2026-09-22",
      "2026-09-23",
      "2026-09-24",
      "2026-09-25",
      "2026-09-26",
      "2026-09-27",
    ]);
  });
});

describe("addDays", () => {
  it("walks forward", () => {
    expect(addDays("2026-09-21", 3)).toBe("2026-09-24");
  });

  it("walks backward across a month boundary", () => {
    expect(addDays("2026-10-01", -1)).toBe("2026-09-30");
  });
});

describe("addWeeks", () => {
  it("moves a Monday to the next Monday", () => {
    expect(addWeeks("2026-09-21", 1)).toBe("2026-09-28");
  });

  it("moves a Monday to the previous Monday", () => {
    expect(addWeeks("2026-09-21", -1)).toBe("2026-09-14");
  });
});
