import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Project } from "@/api/types";
import { ProjectFormDialog } from "./ProjectFormDialog";

const CREATED: Project = {
  id: "proj-new",
  code: "PB-20",
  name: "New Engagement",
  client: "",
  status: "on_track",
  progress_pct: 0,
  budget_minor: 0,
  spent_minor: 0,
  currency: "USD",
  owner_id: null,
  start_date: null,
  due_date: null,
  billable: true,
  open_tasks: 0,
};

afterEach(() => {
  vi.restoreAllMocks();
});

function openCreate(onSaved = vi.fn(), onClose = vi.fn()) {
  render(<ProjectFormDialog project={null} people={[]} onClose={onClose} onSaved={onSaved} />);
  return { user: userEvent.setup(), onSaved, onClose };
}

describe("ProjectFormDialog", () => {
  it("refuses to submit without a valid code and names the rule", async () => {
    const spy = vi.spyOn(client, "createProject");
    const { user } = openCreate();

    await user.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByText(/40 letters, numbers, hyphens or underscores/)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("disables the submit button and blocks a second submit while pending", async () => {
    let resolveCreate: (value: Project) => void = () => undefined;
    vi.spyOn(client, "createProject").mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve;
      }),
    );
    const { user } = openCreate();

    await user.type(screen.getByLabelText("Code"), "PB-20");
    await user.type(screen.getByLabelText("Name"), "New Engagement");
    const submit = screen.getByRole("button", { name: "Create project" });
    await user.click(submit);
    await user.click(submit); // a second click while pending must not fire a second create

    expect(screen.getByRole("button", { name: "Creating…" })).toBeTruthy();
    expect(client.createProject).toHaveBeenCalledTimes(1);

    resolveCreate(CREATED);
    await waitFor(() => expect(screen.getByRole("button", { name: "Create project" })).toBeTruthy());
  });

  it("closes and reports the created project on success", async () => {
    vi.spyOn(client, "createProject").mockResolvedValue(CREATED);
    const { user, onSaved, onClose } = openCreate();

    await user.type(screen.getByLabelText("Code"), "PB-20");
    await user.type(screen.getByLabelText("Name"), "New Engagement");
    await user.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED, "New Engagement created."));
    expect(onClose).toHaveBeenCalled();
  });

  it("maps a 409 on the code field rather than showing a generic banner", async () => {
    const problem = { status: 409, title: "Conflict", detail: "That code is taken", errors: [{ field: "code", code: "duplicate", message: "That code is already in use." }] };
    vi.spyOn(client, "createProject").mockRejectedValue(new ApiError(409, "Conflict", problem));
    const { user } = openCreate();

    await user.type(screen.getByLabelText("Code"), "PB-01");
    await user.type(screen.getByLabelText("Name"), "Helios Web Platform");
    await user.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByText("That code is already in use.")).toBeTruthy();
    // A field-level failure is not repeated as a top-of-form banner.
    expect(screen.queryByText("Conflict")).toBeNull();
  });

  it("shows a 403 as the form-level failure, honestly, rather than hiding it", async () => {
    const problem = { status: 403, title: "Forbidden", detail: "You don't hold engagements.project.write for this organization." };
    vi.spyOn(client, "createProject").mockRejectedValue(new ApiError(403, "Forbidden", problem));
    const { user } = openCreate();

    await user.type(screen.getByLabelText("Code"), "PB-01");
    await user.type(screen.getByLabelText("Name"), "Helios Web Platform");
    await user.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByText("You don't hold engagements.project.write for this organization.")).toBeTruthy();
  });

  it("preserves what was typed after a failed save", async () => {
    vi.spyOn(client, "createProject").mockRejectedValue(new ApiError(500, "Server error", null));
    const { user } = openCreate();

    await user.type(screen.getByLabelText("Code"), "PB-01");
    await user.type(screen.getByLabelText("Name"), "Helios Web Platform");
    await user.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(screen.getByText("Server error")).toBeTruthy());
    expect((screen.getByLabelText("Code") as HTMLInputElement).value).toBe("PB-01");
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Helios Web Platform");
  });
});
