/**
 * The zone rule, pinned.
 *
 * ADR-0000 converts instants at the edge using the relevant location's zone, never the browser's,
 * and the approval screens act on the result — an approver reads a due time and decides. A
 * formatter that quietly used the host zone would still produce a plausible-looking string, which
 * is exactly why it is pinned here rather than checked by looking at a screen.
 */

import { describe, expect, it } from "vitest";

import {
  formatDuration,
  formatInstant,
  formatInZone,
  formatRelative,
  instantToWallClock,
  resolveZone,
  wallClockToInstant,
  zoneAbbreviation,
} from "./datetime";

/** 15 Sep 2026, 13:10 UTC — a Tuesday. 6:40 pm in Kolkata, 9:10 am in New York. */
const INSTANT = "2026-09-15T13:10:00Z";
const SAME_YEAR = new Date("2026-09-16T04:00:00Z");

const LOOKUP = {
  locations: { BLR: "Asia/Kolkata", NYC: "America/New_York" },
  organizationZone: "Asia/Kolkata",
};

describe("formatInZone", () => {
  it("renders the location's wall clock with its zone abbreviation", () => {
    expect(formatInZone(INSTANT, "Asia/Kolkata", "datetime", SAME_YEAR)).toBe(
      "15 Sep, 6:40 pm IST",
    );
  });

  it("adds the weekday when the working day is part of the decision", () => {
    expect(formatInZone(INSTANT, "Asia/Kolkata", "datetime-weekday", SAME_YEAR)).toBe(
      "Tue 15 Sep, 6:40 pm IST",
    );
  });

  it("renders the same instant differently in a different zone — never the host's", () => {
    expect(formatInZone(INSTANT, "America/New_York", "datetime", SAME_YEAR)).toBe(
      "15 Sep, 9:10 am EDT",
    );
  });

  it("adds the year only when the instant is outside the current year in that zone", () => {
    expect(formatInZone(INSTANT, "Asia/Kolkata", "datetime", new Date("2027-01-05T00:00:00Z"))).toBe(
      "15 Sep 2026, 6:40 pm IST",
    );
  });

  it("renders a calendar day with its year and no clock", () => {
    expect(formatInZone("2026-09-12T06:00:00Z", "Asia/Kolkata", "date", SAME_YEAR)).toBe(
      "12 Sep 2026",
    );
  });

  it("renders a time of day with its zone, because a bare 6:40 pm is a bug", () => {
    expect(formatInZone(INSTANT, "Asia/Kolkata", "time", SAME_YEAR)).toBe("6:40 pm IST");
  });

  it("crosses the date boundary in the rendering zone, not in UTC", () => {
    // 19:00 UTC on the 12th is already 00:30 on the 13th in Kolkata.
    expect(formatInZone("2026-09-12T19:00:00Z", "Asia/Kolkata", "date", SAME_YEAR)).toBe(
      "13 Sep 2026",
    );
  });

  it("renders an em dash rather than an Invalid Date", () => {
    expect(formatInZone("not-an-instant", "Asia/Kolkata")).toBe("—");
  });
});

describe("zoneAbbreviation", () => {
  it("prefers a real abbreviation over a GMT offset", () => {
    const date = new Date(INSTANT);
    expect(zoneAbbreviation(date, "Asia/Kolkata")).toBe("IST");
    expect(zoneAbbreviation(date, "America/New_York")).toBe("EDT");
  });

  it("never returns an empty string, even for a zone with no short name", () => {
    expect(zoneAbbreviation(new Date(INSTANT), "Asia/Kathmandu")).not.toBe("");
  });
});

describe("resolveZone", () => {
  it("uses the location's zone when the code matches", () => {
    expect(resolveZone("BLR", LOOKUP)).toEqual({ zone: "Asia/Kolkata", fallback: false });
  });

  it("falls back to organization time for a null code, and says so", () => {
    expect(resolveZone(null, LOOKUP)).toEqual({ zone: "Asia/Kolkata", fallback: true });
  });

  it("falls back for a code that matches no location", () => {
    expect(resolveZone("MARS", LOOKUP).fallback).toBe(true);
  });
});

describe("formatInstant", () => {
  it("labels nothing when the zone came from the location itself", () => {
    const out = formatInstant(INSTANT, "BLR", LOOKUP, "datetime", SAME_YEAR);
    expect(out.text).toBe("15 Sep, 6:40 pm IST");
    expect(out.fallback).toBe(false);
    expect(out.title).toBe("Asia/Kolkata");
  });

  it("labels the fallback visibly, not only in a tooltip", () => {
    const out = formatInstant(INSTANT, null, LOOKUP, "datetime", SAME_YEAR);
    expect(out.text).toBe("15 Sep, 6:40 pm IST · organization time");
    expect(out.title).toBe("Organization time (Asia/Kolkata)");
    expect(out.fallback).toBe(true);
  });

  it("carries the UTC instant for a <time datetime> attribute", () => {
    expect(formatInstant(INSTANT, "BLR", LOOKUP).dateTime).toBe("2026-09-15T13:10:00.000Z");
  });
});

describe("formatRelative", () => {
  const now = new Date("2026-09-16T13:10:00Z");

  it("reads as the primary 'waiting since' value", () => {
    expect(formatRelative("2026-09-14T13:10:00Z", now)).toBe("2 days ago");
    expect(formatRelative("2026-09-16T09:10:00Z", now)).toBe("4 hours ago");
    expect(formatRelative("2026-09-16T13:09:45Z", now)).toBe("just now");
  });

  it("does not pluralise a single unit", () => {
    expect(formatRelative("2026-09-15T13:10:00Z", now)).toBe("1 day ago");
  });

  it("reads forwards for an instant still ahead", () => {
    expect(formatRelative("2026-09-16T19:10:00Z", now)).toBe("in 6 hours");
  });
});

describe("formatDuration", () => {
  it("is always positive, so no user is shown a negative duration", () => {
    expect(formatDuration(-6 * 60 * 60 * 1000)).toBe("6 hours");
  });
});

describe("wall clock ↔ instant", () => {
  it("reads a typed wall clock in the named zone, not the host's", () => {
    expect(wallClockToInstant("2026-09-20T09:00", "Asia/Kolkata")).toBe("2026-09-20T03:30:00.000Z");
    expect(wallClockToInstant("2026-09-20T09:00", "America/New_York")).toBe(
      "2026-09-20T13:00:00.000Z",
    );
  });

  it("uses the offset in force on that date, not a fixed one", () => {
    // Same wall clock, winter: New York is on standard time and an hour further from UTC.
    expect(wallClockToInstant("2026-01-20T09:00", "America/New_York")).toBe(
      "2026-01-20T14:00:00.000Z",
    );
  });

  it("settles on the right side of a daylight-saving jump", () => {
    // 2026-03-08, New York springs forward at 02:00. 03:00 local is already EDT.
    expect(wallClockToInstant("2026-03-08T03:00", "America/New_York")).toBe(
      "2026-03-08T07:00:00.000Z",
    );
  });

  it("round-trips an instant back to the wall clock the field shows", () => {
    expect(instantToWallClock("2026-09-20T03:30:00.000Z", "Asia/Kolkata")).toBe("2026-09-20T09:00");
    expect(instantToWallClock("2026-09-20T03:30:00.000Z", "America/New_York")).toBe(
      "2026-09-19T23:30",
    );
  });

  it("renders an empty field rather than an Invalid Date", () => {
    expect(instantToWallClock("nonsense", "Asia/Kolkata")).toBe("");
    expect(wallClockToInstant("", "Asia/Kolkata")).toBe("");
  });
});
