/**
 * The task-list shell's four built states. One test per state, standing in for the story-per-
 * state convention `@opsgrids/ui` uses in Storybook: this app has no Storybook of its own, so a
 * render test is the equivalent regression pin.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { WorkerTaskRow } from "./TaskListPanel";
import { TaskListPanel } from "./TaskListPanel";

function renderPanel(ui: ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

function task(overrides: Partial<WorkerTaskRow["task"]> = {}): WorkerTaskRow["task"] {
  return {
    id: "task-1",
    order_id: "11111111-2222-3333-4444-555555555555",
    warehouse_id: "wh-1",
    zone_location_id: null,
    status: "ACCEPTED",
    round: 1,
    max_rounds: 3,
    lines: [
      {
        id: "line-1",
        order_line_id: "ol-1",
        inventory_item_id: "item-1",
        sku: "SKU-1",
        source_location_id: "loc-1",
        destination_location_id: "loc-2",
        quantity: "10",
        picked: "0",
        short: "0",
        sequence: 1,
        status: "pending",
      },
    ],
    offers: [],
    exceptions: [],
    created_at: "2026-09-29T09:00:00Z",
    updated_at: "2026-09-29T09:00:00Z",
    ...overrides,
  };
}

describe("TaskListPanel", () => {
  it("loading: announces itself to assistive technology and shows no task content", () => {
    renderPanel(<TaskListPanel status="loading" />);

    const status = screen.getByRole("status");
    expect(status.textContent).toBe("Loading tasks");
    expect(screen.queryByText("No tasks yet")).toBeNull();
  });

  it("empty: explains there are no tasks yet without claiming the feature is broken", () => {
    renderPanel(<TaskListPanel status="empty" />);

    expect(screen.getByText("No tasks yet")).toBeTruthy();
    expect(
      screen.getByText(
        "Pick, pack and dispatch tasks will appear here once your warehouse turns on task offers.",
      ),
    ).toBeTruthy();
  });

  it("error: shows the failure and lets the caller retry", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    renderPanel(
      <TaskListPanel status="error" errorMessage="The network request timed out." onRetry={onRetry} />,
    );

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("Couldn't load your tasks")).toBeTruthy();
    expect(screen.getByText("The network request timed out.")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("error: falls back to a generic message when none is given", () => {
    renderPanel(<TaskListPanel status="error" />);

    expect(screen.getByText("Something went wrong. Check your connection and try again.")).toBeTruthy();
  });

  it("populated: an empty list reads the same as the honest empty state", () => {
    renderPanel(<TaskListPanel status="populated" tasks={[]} />);

    expect(screen.getByText("No tasks yet")).toBeTruthy();
  });

  it("populated: lists a task, its status and its offer expiry, and links to the task", () => {
    const row: WorkerTaskRow = {
      task: task({ status: "OFFERED" }),
      myOffer: {
        id: "offer-1",
        task_id: "task-1",
        worker_id: "worker-1",
        round: 1,
        state: "OFFERED",
        score_breakdown: {},
        offered_at: "2026-09-29T09:00:00Z",
        expires_at: new Date(Date.now() + 5 * 60_000).toISOString(),
        responded_at: null,
      },
    };
    renderPanel(<TaskListPanel status="populated" tasks={[row]} />);

    const link = screen.getByRole("link", { name: /Order 11111111/ });
    expect(link.getAttribute("href")).toBe("/tasks/task-1");
    expect(within(link).getByText("Offered")).toBeTruthy();
    expect(within(link).getByText(/Expires in/)).toBeTruthy();
  });
});
