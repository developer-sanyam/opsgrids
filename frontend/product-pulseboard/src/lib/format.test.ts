import { describe, expect, it } from "vitest";

import { weekFrom } from "@/api/dates";
import { formatWeekRange } from "./format";

describe("formatWeekRange", () => {
  it("compacts a week within one month to a single month/year", () => {
    // en-GB abbreviates September to "Sept", not "Sep" — matching `formatDate`/`formatDayMonth`.
    expect(formatWeekRange(weekFrom("2026-09-21"))).toBe("21–27 Sept 2026");
  });

  it("names both months when the week crosses a boundary", () => {
    expect(formatWeekRange(weekFrom("2026-09-28"))).toBe("28 Sept – 4 Oct 2026");
  });
});
