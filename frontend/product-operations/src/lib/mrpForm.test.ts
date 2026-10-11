import { describe, expect, it } from "vitest";

import { EMPTY_FORM, SOURCES_ERROR, buildMrpRun, lineErrorKey, newLine, whereItRuns } from "./mrpForm";
import type { MrpFormState } from "./mrpForm";

function form(overrides: Partial<MrpFormState> = {}): MrpFormState {
  return { ...EMPTY_FORM, ...overrides };
}

describe("buildMrpRun", () => {
  it("sends the chosen sources and lets the server decide where it runs", () => {
    const { input, errors } = buildMrpRun(form());
    expect(errors).toEqual({});
    expect(input).toEqual({ sources: ["SALES_ORDER", "PRODUCTION_ORDER"], mode: "auto" });
  });

  it("needs at least one source", () => {
    const { input, errors } = buildMrpRun(form({ sales: false, production: false }));
    expect(input).toBeNull();
    expect(errors.sources).toBe(SOURCES_ERROR);
  });

  it("forces the worker when asked, and carries the plan date and notes", () => {
    const { input } = buildMrpRun(form({ background: true, planDate: "2026-11-01", notes: "  week 44  " }));
    expect(input).toEqual({
      sources: ["SALES_ORDER", "PRODUCTION_ORDER"],
      plan_date: "2026-11-01",
      notes: "week 44",
      mode: "worker",
    });
  });

  it("refuses manual demand with no lines", () => {
    const { input, errors } = buildMrpRun(form({ sales: false, production: false, manual: true }));
    expect(input).toBeNull();
    expect(errors.sources).toMatch(/at least one manual demand line/);
  });

  it("sends manual quantities as the typed strings, never numbers", () => {
    const line = { ...newLine("a"), itemId: "item-1", quantity: " 82.500 ", dueDate: "2026-12-01", note: " rush " };
    const { input } = buildMrpRun(form({ manual: true, lines: [line] }));
    expect(input?.manual_demands).toEqual([
      { item_id: "item-1", quantity: "82.500", due_date: "2026-12-01", note: "rush" },
    ]);
    expect(typeof input?.manual_demands?.[0]?.quantity).toBe("string");
    expect(input?.sources).toContain("MANUAL");
  });

  it("flags each bad manual line on its own field", () => {
    const none = newLine("a");
    const decimals = { ...newLine("b"), itemId: "item-2", quantity: "1.2345" };
    const zero = { ...newLine("c"), itemId: "item-3", quantity: "0" };
    const { input, errors } = buildMrpRun(form({ manual: true, lines: [none, decimals, zero] }));
    expect(input).toBeNull();
    expect(errors[lineErrorKey(none, "item")]).toBe("Choose an item.");
    expect(errors[lineErrorKey(none, "quantity")]).toBe("Enter a quantity.");
    expect(errors[lineErrorKey(decimals, "quantity")]).toMatch(/three decimals/);
    expect(errors[lineErrorKey(zero, "quantity")]).toMatch(/greater than zero/);
  });

  it("ignores manual lines when manual demand is off", () => {
    const line = { ...newLine("a"), itemId: "item-1", quantity: "5" };
    const { input } = buildMrpRun(form({ manual: false, lines: [line] }));
    expect(input?.manual_demands).toBeUndefined();
    expect(input?.sources).not.toContain("MANUAL");
  });
});

describe("whereItRuns", () => {
  it("says so before the run is started", () => {
    expect(whereItRuns({ background: true }, null)).toMatch(/background/);
    expect(whereItRuns({ background: false }, { mode_if_auto: "sync" })).toBe("It will be computed now.");
    expect(whereItRuns({ background: false }, { mode_if_auto: "worker" })).toMatch(/lot of open demand/);
    expect(whereItRuns({ background: false }, null)).toBeNull();
  });
});
