import { describe, expect, it } from "vitest";

import type { InspectionStatus } from "@/api/types";
import {
  availableActions,
  formatBounds,
  isDecimal,
  isPositiveDecimal,
  toParamIn,
  validateParams,
} from "./quality";
import type { InspectionPerms, ParamDraft } from "./quality";

const ALL: InspectionPerms = { inspect: true, approve: true, reject: true };
const NONE: InspectionPerms = { inspect: false, approve: false, reject: false };

function inspection(status: InspectionStatus, rejected: string | null = null, disposition = "NONE") {
  return { status, rejected, disposition: disposition as "NONE" | "SCRAP" | "RETURN_TO_VENDOR" };
}

describe("isDecimal / isPositiveDecimal", () => {
  it.each(["0", "40", "40.5", "0.125", "-3", "  7 "])("accepts %j as a decimal", (v) => {
    expect(isDecimal(v)).toBe(true);
  });
  it.each(["", "abc", "1.2345", "1e3", "1,5", ".5", "5."])("refuses %j", (v) => {
    expect(isDecimal(v)).toBe(false);
  });
  it("positive means above zero, in any spelling of zero", () => {
    expect(isPositiveDecimal("0.001")).toBe(true);
    expect(isPositiveDecimal("0")).toBe(false);
    expect(isPositiveDecimal("0.000")).toBe(false);
    expect(isPositiveDecimal("-1")).toBe(false);
  });
});

describe("availableActions mirrors the server's state machine and permission floors", () => {
  it("offers an inspector everything open stock allows, and nothing that decides it", () => {
    const perms = { inspect: true, approve: false, reject: false };
    expect(availableActions(inspection("PENDING"), perms)).toEqual(["record", "hold", "rework"]);
    expect(availableActions(inspection("HOLD"), perms)).toEqual(["record", "rework"]);
    expect(availableActions(inspection("REWORK"), perms)).toEqual(["reinspect"]);
  });

  it("gives a person with no permission nothing, in every state", () => {
    for (const s of ["PENDING", "HOLD", "REWORK", "PASSED", "FAILED", "PARTIAL_PASS"] as const) {
      expect(availableActions(inspection(s, "5"), NONE)).toEqual([]);
    }
  });

  it("splits release (approve) from reject (reject)", () => {
    expect(availableActions(inspection("PENDING"), { ...NONE, approve: true })).toEqual(["release"]);
    expect(availableActions(inspection("PENDING"), { ...NONE, reject: true })).toEqual(["reject"]);
    expect(availableActions(inspection("REWORK"), { ...NONE, approve: true })).toEqual([]);
    expect(availableActions(inspection("REWORK"), { ...NONE, reject: true })).toEqual(["reject"]);
  });

  it("offers nothing on a decided inspection except one disposition of rejected stock, once", () => {
    expect(availableActions(inspection("PASSED", "0"), ALL)).toEqual([]);
    expect(availableActions(inspection("FAILED", "40"), ALL)).toEqual(["dispose"]);
    expect(availableActions(inspection("PARTIAL_PASS", "15"), ALL)).toEqual(["dispose"]);
    expect(availableActions(inspection("PARTIAL_PASS", "15", "SCRAP"), ALL)).toEqual([]);
    expect(availableActions(inspection("FAILED", "0"), ALL)).toEqual([]);
    expect(availableActions(inspection("FAILED", "40"), { ...NONE, inspect: true, approve: true })).toEqual([]);
  });
});

describe("template parameter drafts", () => {
  function draft(over: Partial<ParamDraft> = {}): ParamDraft {
    return { key: "k", name: "GSM", kind: "numeric", unit: "g/m2", min: "170", max: "190", is_critical: false, ...over };
  }

  it("sends bounds as the typed decimal strings, blanks as null, and no bounds for pass/fail", () => {
    expect(toParamIn(draft({ min: " 170.5 ", max: "" }))).toEqual({
      name: "GSM",
      kind: "numeric",
      unit: "g/m2",
      min: "170.5",
      max: null,
      is_critical: false,
    });
    expect(toParamIn(draft({ kind: "pass_fail", min: "1", max: "2" }))).toMatchObject({ min: null, max: null });
  });

  it("explains the first problem in plain words", () => {
    expect(validateParams([draft()])).toBeUndefined();
    expect(validateParams([draft({ name: " " })])).toMatch(/needs a name/);
    expect(validateParams([draft(), draft({ name: "gsm" })])).toMatch(/appears twice/);
    expect(validateParams([draft({ min: "abc" })])).toMatch(/minimum must be a number/);
    expect(validateParams([draft({ max: "1.2345" })])).toMatch(/maximum must be a number/);
  });

  it("compares bounds exactly, not as floats", () => {
    expect(validateParams([draft({ min: "0.3", max: "0.1" })])).toMatch(/minimum is above the maximum/);
    expect(validateParams([draft({ min: "0.1", max: "0.1" })])).toBeUndefined();
    expect(validateParams([draft({ min: "0.002", max: "0.003" })])).toBeUndefined();
  });
});

describe("formatBounds", () => {
  it("reads naturally for every shape of limit", () => {
    expect(formatBounds({ kind: "numeric", min: "170", max: "190" })).toBe("170 – 190");
    expect(formatBounds({ kind: "numeric", min: null, max: "3.5" })).toBe("≤ 3.5");
    expect(formatBounds({ kind: "numeric", min: "170", max: null })).toBe("≥ 170");
    expect(formatBounds({ kind: "numeric", min: null, max: null })).toBe("—");
    expect(formatBounds({ kind: "pass_fail", min: null, max: null })).toBe("Pass / fail");
  });
});
