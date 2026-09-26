import { describe, expect, it } from "vitest";

import { addDays, parseIsoDate, toIsoDate } from "./dates";
import { deviceLabel, formatMoney, linkLabel, percentOf, shareCaption } from "./format";
import { isValidRange } from "./useDateRange";

describe("money", () => {
  it("shows integer minor units in the currency's own notation", () => {
    expect(formatMoney({ amount_minor: 384250, currency: "INR" })).toBe("₹3,842.50");
    expect(formatMoney({ amount_minor: 0, currency: "INR" })).toBe("₹0.00");
  });

  it("uses the currency's real fraction digits, so a zero-decimal currency is not read 100 times small", () => {
    expect(formatMoney({ amount_minor: 5000, currency: "JPY" })).toBe("JP¥5,000");
  });
});

describe("shares", () => {
  it("rounds to one decimal", () => {
    expect(percentOf(11921, 12842)).toBe("92.8%");
    expect(percentOf(1, 3)).toBe("33.3%");
  });

  it("is absent, not zero, when there is nothing to be a share of", () => {
    expect(percentOf(0, 0)).toBeNull();
    expect(shareCaption(5, 0, "sent")).toBeUndefined();
    expect(shareCaption(5, 10, "sent")).toBe("50% of sent");
  });
});

describe("labels", () => {
  it("names devices, with a fallback for one it has not heard of", () => {
    expect(deviceLabel("desktop")).toBe("Desktop");
    expect(deviceLabel("watch")).toBe("Watch");
  });

  it("names a link by host and path, without its query string or fragment", () => {
    expect(linkLabel("https://shop.example.com/sale?token=abc#top")).toBe("shop.example.com/sale");
    expect(linkLabel("https://shop.example.com/")).toBe("shop.example.com");
    expect(linkLabel("not a url")).toBe("not a url");
  });
});

describe("calendar dates", () => {
  it("reads only real dates", () => {
    expect(parseIsoDate("2026-09-30")).not.toBeNull();
    expect(parseIsoDate("2026-02-31")).toBeNull();
    expect(parseIsoDate("2026-9-3")).toBeNull();
    expect(parseIsoDate("nope")).toBeNull();
  });

  it("counts days across a month end", () => {
    const start = parseIsoDate("2026-09-28");
    expect(start).not.toBeNull();
    expect(toIsoDate(addDays(start as Date, 5))).toBe("2026-10-03");
  });

  it("accepts a range only when both ends are dates in order", () => {
    expect(isValidRange("2026-09-01", "2026-09-30")).toBe(true);
    expect(isValidRange("2026-09-01", "2026-09-01")).toBe(true);
    expect(isValidRange("2026-09-30", "2026-09-01")).toBe(false);
    expect(isValidRange("", "2026-09-01")).toBe(false);
  });
});
