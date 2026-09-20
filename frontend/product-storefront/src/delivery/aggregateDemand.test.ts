import { describe, expect, it } from "vitest";

import { aggregateDemand } from "./api";

/**
 * `GET /delivery/demand` returns ADR-0010 §7's counter table raw — **one row per postal code per
 * day per outcome**. The merchant's question is about the range, not the day, so the summing
 * happens in the client. It is arithmetic on someone's expansion plan, so it is tested.
 */

describe("aggregateDemand", () => {
  it("sums a postal code's daily counts across the range", () => {
    const totals = aggregateDemand([
      { postal_code: "421201", outcome: "NO_MAPPING", checks: 10, check_date: "2026-09-17" },
      { postal_code: "421201", outcome: "NO_MAPPING", checks: 24, check_date: "2026-09-18" },
    ]);

    expect(totals).toEqual([
      { postal_code: "421201", outcome: "NO_MAPPING", checks: 34, last_checked_on: "2026-09-18" },
    ]);
  });

  it("keeps the latest date, whatever order the days arrive in", () => {
    const totals = aggregateDemand([
      { postal_code: "421201", outcome: "NO_MAPPING", checks: 1, check_date: "2026-09-19" },
      { postal_code: "421201", outcome: "NO_MAPPING", checks: 1, check_date: "2026-09-02" },
    ]);

    expect(totals[0]?.last_checked_on).toBe("2026-09-19");
  });

  it("keeps outcomes apart — the same code served and turned away is two facts", () => {
    const totals = aggregateDemand([
      { postal_code: "411001", outcome: "SERVED", checks: 5, check_date: "2026-09-18" },
      { postal_code: "411001", outcome: "ZONE_INACTIVE", checks: 7, check_date: "2026-09-18" },
    ]);

    expect(totals).toHaveLength(2);
    // Most-asked first, which is the order the report renders in.
    expect(totals[0]?.outcome).toBe("ZONE_INACTIVE");
  });

  it("orders by range total, not by the busiest single day", () => {
    const totals = aggregateDemand([
      { postal_code: "400703", outcome: "NO_MAPPING", checks: 30, check_date: "2026-09-18" },
      { postal_code: "421201", outcome: "NO_MAPPING", checks: 20, check_date: "2026-09-17" },
      { postal_code: "421201", outcome: "NO_MAPPING", checks: 20, check_date: "2026-09-18" },
    ]);

    expect(totals.map((row) => row.postal_code)).toEqual(["421201", "400703"]);
  });

  it("has nothing to say about an empty range", () => {
    expect(aggregateDemand([])).toEqual([]);
  });
});
