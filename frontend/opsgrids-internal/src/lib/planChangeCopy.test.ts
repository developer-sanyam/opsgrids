import { describe, expect, it } from "vitest";

import type { PlanChangePreview, PlanChangeResult } from "@/api/types";
import {
  directionPhrase,
  failureKind,
  notMadeGuidance,
  previewSentence,
  refusalGuidance,
  resultSentence,
  resultTitle,
} from "./planChangeCopy";

function preview(overrides: Partial<PlanChangePreview> = {}): PlanChangePreview {
  return {
    expected: { state: "active", plan_version_id: "pv_a", pending_plan_version_id: null },
    action: "switch",
    direction: "upgrade",
    effective: "next_period",
    effective_at: "2026-11-01T00:00:00Z",
    would_clear_pending: false,
    entitled_added: [],
    entitled_removed: [],
    usage_priced: false,
    usage_closed_now: false,
    refusal_code: null,
    refusal_detail: null,
    ...overrides,
  };
}

const base = { organizationName: "Zenith Polymers", fromPlan: "Growth", toPlan: "Scale", pendingPlan: null };

describe("previewSentence", () => {
  it.each([
    [
      "a paying subscription waits for its next period, and says nothing is charged before then",
      preview(),
      "Zenith Polymers stays on Growth until 01 Nov 2026 (UTC), then moves to Scale (a price increase). Nothing changes, and nothing is charged, before then.",
    ],
    [
      "a downgrade is a price decrease, also at the next period",
      preview({ direction: "downgrade" }),
      "Zenith Polymers stays on Growth until 01 Nov 2026 (UTC), then moves to Scale (a price decrease). Nothing changes, and nothing is charged, before then.",
    ],
    [
      "an equal-price change applies now and charges nothing",
      preview({ direction: "equal", effective: "now", effective_at: null }),
      "Zenith Polymers moves from Growth to Scale now (the same price). Nothing is charged.",
    ],
    [
      "an equal-price change on a paying, metered subscription says the usage so far is invoiced",
      preview({ direction: "equal", effective: "now", effective_at: null, usage_priced: true, usage_closed_now: true }),
      "Zenith Polymers moves from Growth to Scale now (the same price). No proration is charged, but usage metered so far this period is invoiced at Growth's usage rates; usage from now on is billed at Scale's.",
    ],
    [
      "an equal-price change where only the new plan meters still charges nothing now",
      preview({ direction: "equal", effective: "now", effective_at: null, usage_priced: true, usage_closed_now: false }),
      "Zenith Polymers moves from Growth to Scale now (the same price). Nothing is charged.",
    ],
    [
      "a trial switches now, free",
      preview({ effective: "now", effective_at: null }),
      "Zenith Polymers moves from Growth to Scale now (a price increase). They are on a trial, so nothing is charged.",
    ],
  ])("%s", (_name, p, expected) => {
    expect(previewSentence({ ...base, preview: p })).toBe(expected);
  });

  it("names the change it withdraws", () => {
    const p = preview({ action: "withdraw_scheduled", direction: null, effective: "now", effective_at: null });
    expect(previewSentence({ ...base, preview: p, pendingPlan: "Enterprise" })).toBe(
      "Withdraw the change scheduled to Enterprise — Zenith Polymers stays on Growth.",
    );
  });

  it("never states an amount, a proration or an immediate charge for a paying subscription", () => {
    const text = previewSentence({ ...base, preview: preview() });
    expect(text).not.toMatch(/₹|\$|prorat|charged now|immediately/i);
  });
});

describe("directionPhrase", () => {
  it("has a phrase for each direction and none for a refusal", () => {
    expect(directionPhrase("upgrade")).toBe("a price increase");
    expect(directionPhrase("downgrade")).toBe("a price decrease");
    expect(directionPhrase("equal")).toBe("the same price");
    expect(directionPhrase(null)).toBe("");
  });
});

describe("refusalGuidance", () => {
  it.each([
    "state",
    "cadence",
    "currency",
    "already_pinned",
    "not_offerable",
    "billing_catching_up",
    "state_changed",
    "reserved_reason",
  ])(
    "has guidance for %s",
    (code) => {
      expect(refusalGuidance(code)).toBeTruthy();
    },
  );

  it("says billing catching up does not clear while the run is paused, not 'try again shortly'", () => {
    const text = refusalGuidance("billing_catching_up") ?? "";
    expect(text).toMatch(/paused/);
    expect(text).not.toMatch(/shortly/i);
  });

  it("has nothing to add for a code it does not know", () => {
    expect(refusalGuidance("something_new")).toBeNull();
    expect(refusalGuidance(null)).toBeNull();
  });
});

describe("failureKind", () => {
  it.each([
    [422, "state", "refused"],
    [409, "state_changed", "refused"],
    [409, "billing_catching_up", "refused"],
    [404, "not_found", "refused"],
    [409, "request_in_progress", "in_flight"],
    [503, "outcome_unknown", "unknown"],
    [503, "upstream_refused", "not_made"],
    [503, "catalog_unavailable", "not_made"],
    [503, "preview_unavailable", "not_made"],
    [502, null, "unknown"],
    [500, null, "unknown"],
    [null, null, "unknown"],
  ] as const)("%s %s is %s", (status, code, kind) => {
    expect(failureKind(status, code)).toBe(kind);
  });
});

describe("notMadeGuidance", () => {
  it("never says 'may or may not' — the server said nothing was changed", () => {
    for (const code of ["upstream_refused", "catalog_unavailable", "preview_unavailable", null]) {
      const text = notMadeGuidance(code);
      expect(text).toMatch(/^Nothing was changed\./);
      expect(text).not.toMatch(/may or may not/);
    }
  });

  it("does not suggest retrying a refused service credential", () => {
    expect(notMadeGuidance("upstream_refused")).toMatch(/engineering/);
    expect(notMadeGuidance("upstream_refused")).not.toMatch(/try again/i);
  });
});

describe("results", () => {
  const scheduled: PlanChangeResult = {
    effective: "next_period",
    state: "active",
    plan_version_id: "pv_a",
    pending_plan_version_id: "pv_b",
    effective_at: "2026-11-01T00:00:00Z",
    replayed: false,
  };

  it("reports a scheduled change by its date, from the server's answer", () => {
    expect(resultTitle(scheduled)).toBe("Change scheduled");
    expect(resultSentence("Zenith Polymers", "Scale", scheduled)).toBe(
      "Zenith Polymers moves to Scale on 01 Nov 2026 (UTC). They stay on their current plan until then.",
    );
  });

  it("reports an immediate change as done", () => {
    const now: PlanChangeResult = { ...scheduled, effective: "now", pending_plan_version_id: null, effective_at: null };
    expect(resultTitle(now)).toBe("Plan changed");
    expect(resultSentence("Zenith Polymers", "Scale", now)).toBe("Zenith Polymers is on Scale.");
  });
});
