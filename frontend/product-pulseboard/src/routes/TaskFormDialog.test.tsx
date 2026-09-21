import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Project, Task } from "@/api/types";
import { TaskFormDialog } from "./TaskFormDialog";

const PROJECT: Project = {
  id: "proj-1",
  code: "PB-01",
  name: "Helios Web Platform",
  client: "Helios Retail",
  status: "on_track",
  progress_pct: 10,
  budget_minor: 0,
  spent_minor: 0,
  currency: "USD",
  owner_id: null,
  start_date: null,
  due_date: null,
  billable: true,
  open_tasks: 0,
};

const CREATED: Task = {
  id: "task-1",
  key: "PB-300",
  title: "New card",
  status: "backlog",
  assignee_id: null,
  project_id: "proj-1",
  estimate_hours: 0,
  priority: "medium",
  tags: [],
  due_date: null,
};

afterEach(() => {
  vi.restoreAllMocks();
});

function open(onSaved = vi.fn(), onClose = vi.fn()) {
  render(<TaskFormDialog projects={[PROJECT]} people={[]} onClose={onClose} onSaved={onSaved} />);
  return { user: userEvent.setup(), onSaved, onClose };
}

describe("TaskFormDialog", () => {
  it("refuses an invalid key and names the rule", async () => {
    const spy = vi.spyOn(client, "createTask");
    const { user } = open();

    await user.type(screen.getByLabelText("Key"), "bad key!");
    await user.type(screen.getByLabelText("Title"), "Some card");
    await user.click(screen.getByRole("button", { name: "Create task" }));

    expect(await screen.findByText(/40 letters, numbers, hyphens or underscores/)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("disables submit and blocks a double submit while pending", async () => {
    let resolveCreate: (value: Task) => void = () => undefined;
    vi.spyOn(client, "createTask").mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve;
      }),
    );
    const { user } = open();

    await user.type(screen.getByLabelText("Key"), "PB-300");
    await user.type(screen.getByLabelText("Title"), "New card");
    const submit = screen.getByRole("button", { name: "Create task" });
    await user.click(submit);
    await user.click(submit);

    expect(screen.getByRole("button", { name: "Creating…" })).toBeTruthy();
    expect(client.createTask).toHaveBeenCalledTimes(1);
    resolveCreate(CREATED);
  });

  it("closes and reports the created card on success", async () => {
    vi.spyOn(client, "createTask").mockResolvedValue(CREATED);
    const { user, onSaved, onClose } = open();

    await user.type(screen.getByLabelText("Key"), "PB-300");
    await user.type(screen.getByLabelText("Title"), "New card");
    await user.click(screen.getByRole("button", { name: "Create task" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(onClose).toHaveBeenCalled();
  });

  it("maps a 409 on the key field", async () => {
    const problem = { status: 409, title: "Conflict", errors: [{ field: "key", code: "duplicate", message: "That key is already in use." }] };
    vi.spyOn(client, "createTask").mockRejectedValue(new ApiError(409, "Conflict", problem));
    const { user } = open();

    await user.type(screen.getByLabelText("Key"), "PB-231");
    await user.type(screen.getByLabelText("Title"), "Existing card");
    await user.click(screen.getByRole("button", { name: "Create task" }));

    expect(await screen.findByText("That key is already in use.")).toBeTruthy();
  });

  it("shows a 403 honestly as the form-level failure", async () => {
    const problem = { status: 403, title: "Forbidden", detail: "You don't hold board.task.write for this organization." };
    vi.spyOn(client, "createTask").mockRejectedValue(new ApiError(403, "Forbidden", problem));
    const { user } = open();

    await user.type(screen.getByLabelText("Key"), "PB-300");
    await user.type(screen.getByLabelText("Title"), "New card");
    await user.click(screen.getByRole("button", { name: "Create task" }));

    expect(await screen.findByText("You don't hold board.task.write for this organization.")).toBeTruthy();
  });
});
