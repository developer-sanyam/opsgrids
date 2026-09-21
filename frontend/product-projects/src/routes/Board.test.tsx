import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { ProjectOut, TaskOut } from "@/api/types";
import { BoardPage } from "./Board";

const PROJECT: ProjectOut = {
  id: "proj-1",
  code: "PRJ-104",
  name: "Northwind Trade Portal",
  client: "Northwind Trading",
  owner: { id: "p-01", name: "Aarav Sharma", role: "Delivery Lead" },
  phase: "build",
  health: "on_track",
  progress_pct: 50,
  start_date: "2026-01-01",
  target_date: "2026-06-01",
  budget_minor: 0,
  spent_minor: 0,
  currency: "INR",
  team_size: 5,
  open_tasks: 1,
  milestone_count: 0,
  summary: "",
};

const TASK: TaskOut = {
  id: "task-1",
  project_id: "proj-1",
  project_code: "PRJ-104",
  title: "Bulk-order CSV import",
  assignee: { id: "p-01", name: "Aarav Sharma", role: "Delivery Lead" },
  stage: "backlog",
  priority: "medium",
  due_date: null,
  points: 3,
};

afterEach(() => {
  vi.restoreAllMocks();
});

function renderBoard() {
  vi.spyOn(client, "getAllTasks").mockResolvedValue([TASK]);
  vi.spyOn(client, "getAllProjects").mockResolvedValue([PROJECT]);
  vi.spyOn(client, "getAllMembers").mockResolvedValue([{ ...PROJECT.owner, directory_id: null }]);
  return render(
    <PermissionProvider grants={["projects.board.task.write:OWN"]}>
      <BoardPage />
    </PermissionProvider>,
  );
}

describe("BoardPage — moving a card", () => {
  it("persists a move through PATCH /tasks/{id}, not just local state", async () => {
    const moved: TaskOut = { ...TASK, stage: "in_progress" };
    vi.spyOn(client, "updateTask").mockResolvedValue(moved);
    const user = userEvent.setup();
    renderBoard();

    await screen.findByText("Bulk-order CSV import");
    await user.click(screen.getByRole("button", { name: "More actions for Bulk-order CSV import" }));
    await user.click(screen.getByRole("menuitem", { name: "Move to In Progress" }));

    await waitFor(() =>
      expect(client.updateTask).toHaveBeenCalledWith("task-1", { stage: "in_progress" }),
    );
    expect(await screen.findByText("Bulk-order CSV import moved to In Progress.")).toBeTruthy();
  });

  it("rolls the card back and announces the server's own reason on a 403", async () => {
    const problem = {
      status: 403,
      title: "Forbidden",
      detail: "You can only move cards assigned to you.",
    };
    vi.spyOn(client, "updateTask").mockRejectedValue(new ApiError(403, "Forbidden", problem));
    const user = userEvent.setup();
    renderBoard();

    await screen.findByText("Bulk-order CSV import");
    await user.click(screen.getByRole("button", { name: "More actions for Bulk-order CSV import" }));
    await user.click(screen.getByRole("menuitem", { name: "Move to In Progress" }));

    expect(
      await screen.findByText(
        "The move failed — the card is back where it was: You can only move cards assigned to you.",
      ),
    ).toBeTruthy();
    // Still under Backlog's column — the optimistic patch was rolled back, not kept.
    expect(screen.getByLabelText("Backlog").textContent).toContain("Bulk-order CSV import");
  });
});

describe("BoardPage — permission gating", () => {
  it("hides every write control for a caller with no task.write grant, but still lists cards", async () => {
    vi.spyOn(client, "getAllTasks").mockResolvedValue([TASK]);
    vi.spyOn(client, "getAllProjects").mockResolvedValue([PROJECT]);
    vi.spyOn(client, "getAllMembers").mockResolvedValue([{ ...PROJECT.owner, directory_id: null }]);
    render(
      <PermissionProvider grants={[]}>
        <BoardPage />
      </PermissionProvider>,
    );

    await screen.findByText("Bulk-order CSV import");
    expect(screen.queryByRole("button", { name: "New card" })).toBeNull();
    expect(screen.queryByRole("button", { name: "More actions for Bulk-order CSV import" })).toBeNull();
  });
});
