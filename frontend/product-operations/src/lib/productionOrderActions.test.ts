import { describe, expect, it } from "vitest";

import { availableActions, hasLeftoverWip } from "./productionOrderActions";
import type { ProductionGrants } from "./productionOrderActions";
import { material, order, pick } from "@/test-utils/manufacturingFixtures";

const ALL: ProductionGrants = { write: true, approve: true, issue: true, complete: true };
const NONE: ProductionGrants = { write: false, approve: false, issue: false, complete: false };

describe("availableActions", () => {
  it("offers a draft order approve, edit and cancel", () => {
    expect(availableActions(order({ status: "DRAFT" }), ALL)).toEqual(["approve", "edit", "cancel"]);
  });

  it("offers an approved order start, reserve, issue and cancel", () => {
    expect(availableActions(order({ status: "APPROVED" }), ALL)).toEqual(["start", "reserve", "issue", "cancel"]);
  });

  it("offers an in-progress order output, complete, and wastage and return only while material is in WIP", () => {
    const noWip = availableActions(order({ status: "IN_PROGRESS" }), ALL);
    expect(noWip).toEqual(["reserve", "issue", "output", "complete", "cancel"]);

    const withWip = availableActions(
      order({ status: "IN_PROGRESS", picks: [pick({ wip: "4.5" })], materials: [material({ open_need: "0" })] }),
      ALL,
    );
    expect(withWip).toEqual(["issue", "output", "wastage", "return", "complete", "cancel"]);
  });

  it("offers nothing on a completed or cancelled order", () => {
    expect(availableActions(order({ status: "COMPLETED" }), ALL)).toEqual([]);
    expect(availableActions(order({ status: "CANCELLED" }), ALL)).toEqual([]);
  });

  it("holds each action to its own permission", () => {
    const draft = order({ status: "DRAFT" });
    expect(availableActions(draft, NONE)).toEqual([]);
    expect(availableActions(draft, { ...NONE, approve: true })).toEqual(["approve"]);
    expect(availableActions(draft, { ...NONE, write: true })).toEqual(["edit", "cancel"]);

    const running = order({ status: "IN_PROGRESS", picks: [pick({ wip: "1" })] });
    expect(availableActions(running, { ...NONE, complete: true })).toEqual(["output", "complete"]);
    expect(availableActions(running, { ...NONE, issue: true })).toEqual(["reserve", "issue", "wastage", "return"]);
  });

  it("does not offer Reserve when nothing is left to reserve", () => {
    const covered = order({ status: "APPROVED", materials: [material({ open_need: "0" })] });
    expect(availableActions(covered, ALL)).not.toContain("reserve");
  });
});

describe("hasLeftoverWip", () => {
  it("is true only when some pick still holds material in WIP", () => {
    expect(hasLeftoverWip({ picks: [pick({ wip: "0" })] })).toBe(false);
    expect(hasLeftoverWip({ picks: [pick({ wip: "0.001" })] })).toBe(true);
  });
});
