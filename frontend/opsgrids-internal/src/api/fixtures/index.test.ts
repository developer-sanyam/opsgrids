import { describe, expect, it } from "vitest";

import * as fixtures from "./index";
import { simulateOverride } from "./index";

describe("the barrel client.ts destructures via a dynamic import", () => {
  it("re-exports every binding client.ts's fixtures branches read off it", () => {
    const required = [
      "PRODUCTS",
      "PLANS",
      "PLAN_VERSIONS",
      "ORGANIZATIONS",
      "SUBSCRIPTIONS",
      "CREDIT_LEDGER",
      "AUDIT_LOG",
      "buildOverviewMetrics",
      "organizationDetail",
      "pricingForOrg",
      "applyFixturePrice",
      "simulateOverride",
    ] as const;
    for (const name of required) {
      expect(fixtures[name], `fixtures barrel is missing "${name}"`).toBeDefined();
    }
  });
});

describe("simulateOverride", () => {
  it("accepts every override kind and echoes the organization it targeted", () => {
    const result = simulateOverride({
      kind: "suspend",
      organization_id: "org_1",
      reason: "Non-payment",
    });
    expect(result.status).toBe("accepted");
    expect(result.audit.organization_id).toBe("org_1");
    expect(result.audit.action).toBe("override.suspend");
    expect(result.audit.result).toBe("success");
  });
});
