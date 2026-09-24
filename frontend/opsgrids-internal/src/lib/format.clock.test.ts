import { describe, expect, it } from "vitest";

import { daysUntil, relativeDays } from "./format";

/**
 * Both helpers used to default `now` to the fixtures' fixed "today" (2026-08-24), so production
 * measured every trial end and grant expiry against a date in the past — a trial nine days out
 * read as six weeks away and never reached Overview's "Needs attention". The default must be the
 * real clock; tests that need a fixed instant pass their own.
 */
describe("the default clock is the real one", () => {
  it("daysUntil measures from now", () => {
    const inNineDays = new Date(Date.now() + 9 * 86_400_000).toISOString();
    expect(daysUntil(inNineDays)).toBe(9);
  });

  it("relativeDays measures from now", () => {
    const threeDaysAgo = new Date(Date.now() - 3 * 86_400_000).toISOString();
    expect(relativeDays(threeDaysAgo)).toBe("3 days ago");
  });
});
