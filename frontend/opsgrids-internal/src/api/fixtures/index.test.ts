import { describe, expect, it } from "vitest";

import * as fixtures from "./index";
import { simulateOverride } from "./index";

describe("the barrel client.ts destructures via a dynamic import", () => {
  it("re-exports every binding client.ts's fixtures branches read off it", () => {
    const required = [
      "PRODUCTS",
      "PLAN_VERSIONS",
      "CREDIT_LEDGER",
      "AUDIT_LOG",
      "buildOverviewMetrics",
      "buildMetricsSeries",
      "buildOrganizationsOut",
      "buildSubscriptionsOut",
      "buildUsageListOut",
      "buildLedgerPage",
      "buildAuditListOut",
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
  it("accepts a suspend override and echoes the organization and delegation target it targeted", () => {
    const result = simulateOverride({
      action: "suspend",
      organization_id: "org_1",
      subscription_id: "sub_1",
      reason: "Non-payment",
    });
    expect(result.action).toBe("suspend");
    expect(result.organization_id).toBe("org_1");
    expect(result.delegated_to).toBe("subscription");
  });

  it("accepts a reactivate override and delegates to subscription", () => {
    const result = simulateOverride({
      action: "reactivate",
      organization_id: "org_1",
      subscription_id: "sub_1",
      reason: "Payment received",
    });
    expect(result.action).toBe("reactivate");
    expect(result.delegated_to).toBe("subscription");
  });

  it("accepts a comp_grant override and delegates to billing", () => {
    const result = simulateOverride({
      action: "comp_grant",
      organization_id: "org_1",
      amount_minor: 5000,
      currency: "INR",
      reason: "Goodwill",
    });
    expect(result.action).toBe("comp_grant");
    expect(result.delegated_to).toBe("billing");
  });
});
