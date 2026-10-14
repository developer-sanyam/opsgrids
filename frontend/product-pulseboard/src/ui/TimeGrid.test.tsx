import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Person, TimesheetRow } from "@/api/types";

import { TimeGrid } from "./TimeGrid";

/**
 * The weekly grid is a table from 768px and a card per person below, where its 880px of columns
 * and far-right actions cannot be reached. The card carries the same facts and the same action.
 */

const WEEK = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05", "2026-06-06", "2026-06-07"];

const PEOPLE: Person[] = [
  { id: "p-01", uuid: "u-1", name: "Meera Iyer", role: "Lead", location: "Pune", capacity_hours: 40 },
];

function row(status: TimesheetRow["status"]): TimesheetRow {
  return {
    person_id: "p-01",
    week_start: WEEK[0] ?? "",
    status,
    cells: WEEK.map((date, i) => ({
      date,
      entered_hours: i < 2 ? 8 : 0,
      billable_hours: i < 2 ? 6 : 0,
    })),
    entries: [],
  };
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

function renderGrid(status: TimesheetRow["status"], onAdvance = vi.fn()) {
  render(
    <TimeGrid
      rows={[row(status)]}
      people={PEOPLE}
      weekDates={WEEK}
      today="2026-06-02"
      canWrite
      busyPersonId={null}
      onAdvance={onAdvance}
      onReopen={vi.fn()}
      onEditWeek={vi.fn()}
    />,
  );
  return onAdvance;
}

describe("TimeGrid on a phone", () => {
  it("shows a card per person with the days worked, total, status and the action inline", async () => {
    stubViewport(390);
    const onAdvance = renderGrid("draft");

    expect(screen.queryByRole("table")).toBeNull();
    const list = screen.getByRole("list", { name: "Weekly timesheets by person" });
    const card = within(list).getByRole("listitem");

    expect(within(card).getByRole("heading", { name: "Meera Iyer" })).toBeTruthy();
    expect(within(card).getByText("Draft")).toBeTruthy();
    // Two days with hours are listed; the empty days are not.
    expect(within(card).getAllByRole("definition")).toHaveLength(2);
    expect(within(card).getByText("16h")).toBeTruthy();

    await userEvent.click(within(card).getByRole("button", { name: "Submit" }));
    expect(onAdvance).toHaveBeenCalledTimes(1);
  });

  it("offers Reopen, not Edit week, on a submitted sheet", () => {
    stubViewport(390);
    renderGrid("submitted");
    expect(screen.getByRole("button", { name: "Reopen" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit week" })).toBeNull();
  });
});

describe("TimeGrid from 768px", () => {
  it("stays a table", () => {
    stubViewport(1280);
    renderGrid("draft");
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Weekly timesheets by person" })).toBeNull();
  });
});
