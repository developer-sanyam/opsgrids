import { describe, expect, it } from "vitest";

import { outcomePhrase, predictEffective, summarySentence } from "./pricingCopy";

describe("predictEffective", () => {
  it("is not_subscribed with no subscription, regardless of the checkbox", () => {
    expect(predictEffective(false, false)).toBe("not_subscribed");
    expect(predictEffective(false, true)).toBe("not_subscribed");
  });

  it("is now when subscribed and applying to the subscription", () => {
    expect(predictEffective(true, true)).toBe("now");
  });

  it("is next_period when subscribed but not applying yet", () => {
    expect(predictEffective(true, false)).toBe("next_period");
  });
});

describe("outcomePhrase", () => {
  it.each([
    ["now", "applies now"],
    ["next_period", "applies from the next period"],
    ["not_subscribed", "applies when they subscribe"],
  ] as const)("%s -> %s", (effective, phrase) => {
    expect(outcomePhrase(effective)).toBe(phrase);
  });
});

describe("summarySentence", () => {
  it("matches the approved copy exactly, word for word", () => {
    expect(summarySentence("OpsGrids CRM", "Zenith Polymers", 249_950, "INR", "month", "now")).toBe(
      "Set OpsGrids CRM for Zenith Polymers to ₹2,499.50 per month — applies now.",
    );
  });

  it("says per year for a yearly interval", () => {
    expect(summarySentence("OpsGrids HR", "Lotus Manufacturing", 1_200_000, "INR", "year", "next_period")).toBe(
      "Set OpsGrids HR for Lotus Manufacturing to ₹12,000.00 per year — applies from the next period.",
    );
  });

  it("names the not-yet-subscribed outcome", () => {
    expect(summarySentence("OpsGrids Procure", "Corveto Logistics", 45_000, "INR", "month", "not_subscribed")).toBe(
      "Set OpsGrids Procure for Corveto Logistics to ₹450.00 per month — applies when they subscribe.",
    );
  });
});
