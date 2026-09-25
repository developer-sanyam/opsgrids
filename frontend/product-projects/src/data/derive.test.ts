import { describe, expect, it } from "vitest";

import type { MilestoneOut, TaskOut } from "@/api/types";
import { milestonesDue, needsAttention, overdueTasks, sortByDueDate } from "./derive";

const PERSON = { id: "p-01", name: "Aarav Sharma", role: "Delivery Lead" };

function milestone(overrides: Partial<MilestoneOut>): MilestoneOut {
  return {
    id: "m-1",
    project_id: "proj-1",
    project_code: "PRJ-1",
    project_name: "Test Project",
    title: "A milestone",
    owner: PERSON,
    due_date: "2026-09-25",
    status: "on_track",
    progress_pct: 0,
    ...overrides,
  };
}

function task(overrides: Partial<TaskOut>): TaskOut {
  return {
    id: "t-1",
    project_id: "proj-1",
    project_code: "PRJ-1",
    title: "A task",
    assignee: PERSON,
    stage: "in_progress",
    priority: "medium",
    due_date: "2026-09-20",
    points: 0,
    ...overrides,
  };
}

describe("milestonesDue", () => {
  const now = "2026-09-21";

  it("counts an unfinished milestone due within the window", () => {
    const rows = [milestone({ due_date: "2026-09-25" })];
    expect(milestonesDue(rows, now)).toBe(1);
  });

  it("never counts a completed milestone", () => {
    const rows = [milestone({ due_date: "2026-09-22", status: "completed" })];
    expect(milestonesDue(rows, now)).toBe(0);
  });

  it("never counts an undated milestone — there is no date to judge it against", () => {
    const rows = [milestone({ due_date: null })];
    expect(milestonesDue(rows, now)).toBe(0);
  });
});

describe("needsAttention", () => {
  it("keeps only slipping and at-risk milestones, soonest first", () => {
    const rows = [
      milestone({ id: "m-late", status: "at_risk", due_date: "2026-10-01" }),
      milestone({ id: "m-soon", status: "slipping", due_date: "2026-09-22" }),
      milestone({ id: "m-fine", status: "on_track", due_date: "2026-09-23" }),
    ];
    expect(needsAttention(rows).map((m) => m.id)).toEqual(["m-soon", "m-late"]);
  });
});

describe("overdueTasks", () => {
  const now = "2026-09-25";

  it("counts an unfinished task whose due date has passed", () => {
    const rows = [task({ due_date: "2026-09-20" })];
    expect(overdueTasks(rows, now)).toBe(1);
  });

  it("never counts a done task, however late", () => {
    const rows = [task({ due_date: "2026-09-01", stage: "done" })];
    expect(overdueTasks(rows, now)).toBe(0);
  });

  it("never counts an undated task — there is no date it could have missed", () => {
    const rows = [task({ due_date: null })];
    expect(overdueTasks(rows, now)).toBe(0);
  });

  it("does not count a task due today or in the future", () => {
    const rows = [task({ due_date: "2026-09-25" }), task({ due_date: "2026-09-26" })];
    expect(overdueTasks(rows, now)).toBe(0);
  });
});

describe("sortByDueDate", () => {
  it("sorts an undated milestone after every dated one, not before", () => {
    const rows = [
      milestone({ id: "m-none", due_date: null }),
      milestone({ id: "m-later", due_date: "2026-10-01" }),
      milestone({ id: "m-sooner", due_date: "2026-09-22" }),
    ];
    expect(sortByDueDate(rows).map((m) => m.id)).toEqual(["m-sooner", "m-later", "m-none"]);
  });
});
