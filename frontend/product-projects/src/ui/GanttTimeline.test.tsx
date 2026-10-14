import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MilestoneOut, ProjectOut } from "@/api/types";

import { GanttTimeline } from "./GanttTimeline";

/**
 * Below 768px the Gantt (a 216px label column plus a month scale) gives way to a card per project
 * that writes out what the bars draw; from 768px up the chart stays, its scroll area a keyboard
 * reachable region, and health is text and not just a colour.
 */

const OWNER = { id: "p-01", name: "Meera Iyer", role: "Delivery lead", directory_id: null };

function project(over: Partial<ProjectOut>): ProjectOut {
  return {
    id: "proj-1",
    code: "PRJ-104",
    name: "Customer portal redesign",
    client: "Northwind Traders",
    owner: OWNER,
    phase: "build",
    health: "at_risk",
    progress_pct: 45,
    start_date: "2026-03-02",
    target_date: "2026-09-30",
    budget_minor: 250000000,
    spent_minor: 112000000,
    currency: "INR",
    team_size: 6,
    open_tasks: 12,
    milestone_count: 2,
    summary: "",
    ...over,
  } as ProjectOut;
}

function milestone(over: Partial<MilestoneOut>): MilestoneOut {
  return {
    id: "ms-1",
    project_id: "proj-1",
    project_code: "PRJ-104",
    project_name: "Customer portal redesign",
    title: "Beta launch",
    owner: OWNER,
    due_date: "2026-07-15",
    status: "due_soon",
    progress_pct: 60,
    ...over,
  } as MilestoneOut;
}

function stubViewport(width: number) {
  vi.stubGlobal("matchMedia", (query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    return {
      matches: min?.[1] !== undefined ? width >= Number(min[1]) : false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    };
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const projects = [project({})];
const milestones = [
  milestone({}),
  milestone({ id: "ms-2", title: "Kick-off", due_date: "2026-04-01", status: "completed" }),
  milestone({ id: "ms-3", title: "Undated item", due_date: null }),
];

describe("GanttTimeline on a phone", () => {
  it("lists each project with dates, health as text and its dated milestones in order", () => {
    stubViewport(390);
    render(<GanttTimeline projects={projects} milestones={milestones} now="2026-06-01" />);

    expect(screen.queryByRole("region", { name: /Project timeline/ })).toBeNull();
    const list = screen.getByRole("list", { name: "Projects on the timeline" });
    const card = within(list).getAllByRole("listitem")[0];
    if (!card) throw new Error("no project card");

    expect(within(card).getByRole("heading", { name: "Customer portal redesign" })).toBeTruthy();
    expect(within(card).getByText("At risk")).toBeTruthy();
    expect(within(card).getByText(/PRJ-104/)).toBeTruthy();

    const titles = within(card)
      .getAllByText(/Kick-off|Beta launch/)
      .map((node) => node.textContent);
    expect(titles).toEqual(["Kick-off", "Beta launch"]);
    expect(within(card).queryByText("Undated item")).toBeNull();
  });
});

describe("GanttTimeline from 768px", () => {
  it("keeps the chart in a labelled, focusable scroll region and states health in words", () => {
    stubViewport(1280);
    render(<GanttTimeline projects={projects} milestones={milestones} now="2026-06-01" />);

    const region = screen.getByRole("region", { name: /Project timeline/ });
    expect(region.getAttribute("tabindex")).toBe("0");
    expect(screen.queryByRole("list", { name: "Projects on the timeline" })).toBeNull();
    expect(within(region).getByText("At risk · 45%")).toBeTruthy();
  });
});
