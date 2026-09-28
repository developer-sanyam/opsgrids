/**
 * The task-list shell's three built states (there is no "populated" one yet — see the component's
 * own doc comment). One test per state, standing in for the story-per-state convention `@opsgrids
 * /ui` uses in Storybook: this app has no Storybook of its own, so a render test is the
 * equivalent regression pin.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TaskListPanel } from "./TaskListPanel";

describe("TaskListPanel", () => {
  it("loading: announces itself to assistive technology and shows no task content", () => {
    render(<TaskListPanel status="loading" />);

    const status = screen.getByRole("status");
    expect(status.textContent).toBe("Loading tasks");
    expect(screen.queryByText("No tasks yet")).toBeNull();
  });

  it("empty: explains there are no tasks yet without claiming the feature is broken", () => {
    render(<TaskListPanel status="empty" />);

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
    render(<TaskListPanel status="error" errorMessage="The network request timed out." onRetry={onRetry} />);

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("Couldn't load your tasks")).toBeTruthy();
    expect(screen.getByText("The network request timed out.")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("error: falls back to a generic message when none is given", () => {
    render(<TaskListPanel status="error" />);

    expect(screen.getByText("Something went wrong. Check your connection and try again.")).toBeTruthy();
  });
});
