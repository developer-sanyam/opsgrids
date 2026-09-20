import { describe, expect, it } from "vitest";

import type { Project } from "@/api/types";
import { needsAttention } from "./derive";

function project(overrides: Partial<Project>): Project {
  return {
    id: "p1",
    code: "PB-01",
    name: "Helios Web Platform",
    client: "Helios Retail",
    status: "on_track",
    progress_pct: 50,
    budget_minor: 1000,
    spent_minor: 500,
    currency: "USD",
    owner_id: "p01",
    start_date: "2026-01-01",
    due_date: "2026-12-31",
    billable: true,
    open_tasks: 1,
    ...overrides,
  };
}

describe("needsAttention", () => {
  it("never flags a project with no due date as overdue", () => {
    const items = needsAttention([project({ due_date: null })], "2026-09-21");
    expect(items).toEqual([]);
  });

  it("still flags a project whose real due date has passed", () => {
    const items = needsAttention([project({ due_date: "2026-01-01" })], "2026-09-21");
    expect(items).toHaveLength(1);
    expect(items[0]?.severity).toBe("overdue");
  });

  it("treats a null owner as a representable state, not a crash", () => {
    expect(() => needsAttention([project({ owner_id: null, status: "blocked" })], "2026-09-21")).not.toThrow();
  });
});
