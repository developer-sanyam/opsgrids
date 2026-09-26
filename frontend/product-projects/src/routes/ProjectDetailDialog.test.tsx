/**
 * `ProjectDetailDialog`: budget and spend are shown in the currency's own decimals. A yen project
 * imported or entered as 1500000 is ¥15,00,000, and a dinar project of 12345 minor units is
 * 12.345, not 123.45.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import type { ProjectOut } from "@/api/types";
import { ProjectDetailDialog } from "./ProjectDetailDialog";

function project(over: Partial<ProjectOut>): ProjectOut {
  return {
    id: "proj-1",
    code: "PRJ-104",
    name: "Customer portal redesign",
    client: "Northwind Traders",
    owner: { id: "p-01", name: "Meera Iyer", role: "Delivery lead" },
    phase: "build",
    health: "on_track",
    progress_pct: 45,
    start_date: "2026-03-02",
    target_date: "2026-09-30",
    budget_minor: 250_000_000,
    spent_minor: 112_000_000,
    currency: "INR",
    team_size: 6,
    open_tasks: 12,
    milestone_count: 0,
    summary: "",
    ...over,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function amountsOf(shown: ProjectOut): { budget: string; spent: string } {
  vi.spyOn(client, "getAllMilestones").mockResolvedValue([]);
  render(
    <ProjectDetailDialog
      project={shown}
      onClose={vi.fn()}
      onEdit={vi.fn()}
      onDelete={vi.fn()}
      onAddMilestone={vi.fn()}
    />,
  );
  const of = (label: string) => screen.getByText(label).nextElementSibling?.textContent ?? "";
  return { budget: of("Budget"), spent: of("Spent") };
}

describe("ProjectDetailDialog: budget and spend", () => {
  it.each([
    ["INR", 250_000_000, 112_000_000, "₹25,00,000", "₹11,20,000 · 45%"],
    ["USD", 12_000_050, 13_150_000, "$1,20,000.50", "$1,31,500 · 110%"],
    ["JPY", 1_500_000, 250_000, "JP¥15,00,000", "JP¥2,50,000 · 17%"],
    ["KWD", 12_345, 5_000, "KWD 12.345", "KWD 5 · 41%"],
  ])("shows a %s project's budget and spend by that currency's decimals", (currency, budget, spent, shownBudget, shownSpent) => {
    expect(amountsOf(project({ currency, budget_minor: budget, spent_minor: spent }))).toEqual({
      budget: shownBudget,
      spent: shownSpent,
    });
  });
});
