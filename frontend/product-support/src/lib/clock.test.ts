import { describe, expect, it } from "vitest";

import { now } from "./clock";
import { USE_FIXTURES } from "@/api/client";
import { FIXTURE_NOW } from "@/api/fixtures";

describe("now", () => {
  it("reads the real clock in a live build — the bug this guards against is a frozen NOW " +
    "constant reaching production and reporting every SLA against a fixed instant forever", () => {
    expect(USE_FIXTURES).toBe(false);
    const delta = Math.abs(now().getTime() - Date.now());
    expect(delta).toBeLessThan(1000);
    expect(now().getTime()).not.toBe(FIXTURE_NOW.getTime());
  });
});
