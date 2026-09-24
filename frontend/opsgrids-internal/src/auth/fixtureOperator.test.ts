import { describe, expect, it } from "vitest";

import { FIXTURE_OPERATOR, FIXTURE_TOKEN } from "./fixtureOperator";

describe("the fixtures-mode stand-in operator", () => {
  it("holds a live role, so a write-gated hint like \"Set price\" still shows in fixtures mode", () => {
    expect(FIXTURE_OPERATOR.operator_roles).toContain("owner");
  });

  it("carries a non-empty token so the session shape round-trips", () => {
    expect(FIXTURE_TOKEN.length).toBeGreaterThan(0);
  });
});
