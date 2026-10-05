import { describe, expect, it } from "vitest";

import type { AddProductOption, AddProductResult } from "@/api/types";
import {
  addPreviewSentence,
  addResultSentence,
  addResultTitle,
  endOfIstDay,
  extendPreviewSentence,
  extensionWindow,
  formatIst,
  formatIstDate,
  istDate,
  staffRefusalGuidance,
  totalTrialDays,
} from "./staffSubscriptionCopy";

/**
 * The Add product and Extend trial words and the IST arithmetic, table-driven (ADR-0030). The IST
 * cases are the ones that bite an operator: an instant after 18:30 UTC is already the next day in
 * India, and a cap that falls early on its IST day leaves that day's 23:59 out of reach.
 */

describe("IST dates", () => {
  it.each([
    ["2026-11-03T18:29:59Z", "2026-11-03"],
    ["2026-11-03T18:30:00Z", "2026-11-04"],
    ["2026-11-03T00:00:00+05:30", "2026-11-03"],
    ["2026-12-31T20:00:00Z", "2027-01-01"],
  ])("%s falls on %s in IST", (iso, date) => {
    expect(istDate(iso)).toBe(date);
  });

  it("names the last second of an IST day with its zone", () => {
    expect(endOfIstDay("2026-11-03")).toBe("2026-11-03T23:59:59+05:30");
    expect(new Date(endOfIstDay("2026-11-03")).toISOString()).toBe("2026-11-03T18:29:59.000Z");
  });

  it("formats an instant in IST and says so", () => {
    expect(formatIst("2026-11-03T18:29:59Z")).toBe("03 Nov 2026, 23:59 IST");
    expect(formatIst("2026-10-20T09:00:00.123456Z")).toBe("20 Oct 2026, 14:30 IST");
    expect(formatIst(null)).toBe("—");
    expect(formatIst("not a date")).toBe("—");
    expect(formatIstDate("2026-11-03")).toBe("03 Nov 2026");
  });
});

describe("the extension window", () => {
  const NOW = new Date("2026-10-06T06:00:00Z"); // 11:30 IST on the 6th

  it("runs from the day after the current end to the last day the cap allows", () => {
    // Ends 20 Oct 14:30 IST; cap 30 Dec 00:00 UTC = 05:30 IST on the 30th, so the 29th is the last
    // whole day.
    expect(extensionWindow("2026-10-20T09:00:00Z", "2026-12-30T00:00:00Z", NOW)).toEqual({
      min: "2026-10-20",
      max: "2026-12-29",
    });
  });

  it("starts today when the trial already lapsed", () => {
    expect(extensionWindow("2026-09-05T00:00:00Z", "2026-11-04T00:00:00Z", NOW)).toEqual({
      min: "2026-10-06",
      max: "2026-11-03",
    });
  });

  it("skips a day whose end is not after the current end", () => {
    // Ends at 23:59:59 IST on the 20th already: the 20th itself is no extension.
    expect(extensionWindow("2026-10-20T18:29:59Z", "2026-12-30T18:29:59Z", NOW)).toEqual({
      min: "2026-10-21",
      max: "2026-12-30",
    });
  });

  it("is empty at the cap, and without one", () => {
    expect(extensionWindow("2026-12-29T18:29:59Z", "2026-12-30T00:00:00Z", NOW)).toBeNull();
    expect(extensionWindow("2026-10-20T09:00:00Z", null, NOW)).toBeNull();
  });

  it("reads the cap as days from the trial's start", () => {
    expect(totalTrialDays("2026-10-01T09:00:00Z", "2026-12-30T09:00:00Z")).toBe(90);
    expect(totalTrialDays(null, "2026-12-30T09:00:00Z")).toBeNull();
  });
});

describe("refusal guidance", () => {
  it.each([
    ["already_subscribed", /cannot be reopened or replaced yet/],
    ["one_time", /never billed/],
    ["org_currency_mismatch", /one currency/],
    ["trial_not_offered", /sells no trial/],
    ["charge_not_acknowledged", /billing starts now/],
    ["not_trial", /still trialling/],
    ["claim_in_flight", /already converting/],
    ["trial_cap", /longest a trial may run/],
    ["trial_not_extended", /after the trial's current end/],
    ["state_changed", /not the way it was/],
    ["reserved_reason", /in your own words/],
  ])("%s says what to do next", (code, pattern) => {
    expect(staffRefusalGuidance(code)).toMatch(pattern);
  });

  it("has nothing to add for a code it does not know", () => {
    expect(staffRefusalGuidance("something_new")).toBeNull();
    expect(staffRefusalGuidance(null)).toBeNull();
  });
});

const OPTION: AddProductOption = {
  plan_id: "pl",
  plan_name: "Growth",
  plan_version_id: "pv",
  version_no: 2,
  billing_interval: "month",
  currency: "INR",
  is_bespoke: false,
  recurring_minor: 99_900,
  prices: [],
  bundled_products: null,
  trial_days: 21,
  offers_trial: true,
  overlapping_products: [],
  blocked_code: null,
  blocked_reason: null,
};

describe("sentences", () => {
  it("a trial add says nothing is charged until it ends", () => {
    expect(addPreviewSentence({ organizationName: "Zenith", productName: "OpsGrids CRM", option: OPTION, trial: true })).toBe(
      "Zenith gets OpsGrids CRM on Growth, starting with a 21-day trial. Nothing is charged until the trial ends; the billing run then converts it.",
    );
    expect(
      addPreviewSentence({
        organizationName: "Zenith",
        productName: "OpsGrids CRM",
        option: { ...OPTION, trial_days: null },
        trial: true,
      }),
    ).toMatch(/the plan's standard trial/);
  });

  it("a paid add says billing starts now and nothing checks the mandate", () => {
    const sentence = addPreviewSentence({ organizationName: "Zenith", productName: "OpsGrids CRM", option: OPTION, trial: false });
    expect(sentence).toMatch(/paying from today/);
    expect(sentence).toMatch(/first month is claimed for billing immediately/);
    expect(sentence).toMatch(/no payment mandate is checked/);
    expect(sentence).toMatch(/past due/);
  });

  it("the result says what the server did", () => {
    const trial: AddProductResult = {
      subscription_id: "s",
      product_key: "crm",
      state: "trial",
      plan_version_id: "pv",
      currency: "INR",
      trial_ends_at: "2026-10-20T09:00:00Z",
      current_period_start: "2026-10-06T09:00:00Z",
      current_period_end: "2026-11-05T09:00:00Z",
      entitled_products: ["crm"],
      charge_claimed: false,
      replayed: false,
    };
    expect(addResultTitle(trial)).toBe("Trial started");
    expect(addResultSentence("Zenith", "OpsGrids CRM", "Growth", trial)).toBe(
      "Zenith has OpsGrids CRM on Growth, trialling until 20 Oct 2026, 14:30 IST.",
    );
    const paid = { ...trial, state: "active", trial_ends_at: null, charge_claimed: true };
    expect(addResultTitle(paid)).toBe("Product added");
    expect(addResultSentence("Zenith", "OpsGrids CRM", "Growth", paid)).toMatch(/first period has been claimed/);
  });

  it("an extension names both ends in IST", () => {
    expect(extendPreviewSentence("Zenith", "OpsGrids HR", "2026-10-20T09:00:00Z", endOfIstDay("2026-11-03"))).toBe(
      "Zenith's OpsGrids HR trial will end at 03 Nov 2026, 23:59 IST instead of 20 Oct 2026, 14:30 IST. Nothing is charged before then; the billing run converts it at the new end.",
    );
  });
});
