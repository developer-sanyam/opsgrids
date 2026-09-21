import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Person } from "@/api/types";
import { PersonFormDialog } from "./PersonFormDialog";

const CREATED: Person = {
  id: "p20",
  uuid: "uuid-20",
  name: "New Hire",
  role: "",
  location: "",
  capacity_hours: 40,
};

afterEach(() => {
  vi.restoreAllMocks();
});

function openCreate(onSaved = vi.fn(), onClose = vi.fn()) {
  render(<PersonFormDialog person={null} onClose={onClose} onSaved={onSaved} />);
  return { user: userEvent.setup(), onSaved, onClose };
}

describe("PersonFormDialog", () => {
  it("refuses to submit without a name", async () => {
    const spy = vi.spyOn(client, "createPerson");
    const { user } = openCreate();

    await user.type(screen.getByLabelText("Reference"), "p20");
    await user.click(screen.getByRole("button", { name: "Add to roster" }));

    expect(await screen.findByText("Give this person a name.")).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("disables submit and blocks a double submit while pending", async () => {
    let resolveCreate: (value: Person) => void = () => undefined;
    vi.spyOn(client, "createPerson").mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve;
      }),
    );
    const { user } = openCreate();

    await user.type(screen.getByLabelText("Reference"), "p20");
    await user.type(screen.getByLabelText("Name"), "New Hire");
    const submit = screen.getByRole("button", { name: "Add to roster" });
    await user.click(submit);
    await user.click(submit);

    expect(screen.getByRole("button", { name: "Adding…" })).toBeTruthy();
    expect(client.createPerson).toHaveBeenCalledTimes(1);
    resolveCreate(CREATED);
  });

  it("closes and reports the created person on success", async () => {
    vi.spyOn(client, "createPerson").mockResolvedValue(CREATED);
    const { user, onSaved, onClose } = openCreate();

    await user.type(screen.getByLabelText("Reference"), "p20");
    await user.type(screen.getByLabelText("Name"), "New Hire");
    await user.click(screen.getByRole("button", { name: "Add to roster" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED, "New Hire added to the roster."));
    expect(onClose).toHaveBeenCalled();
  });

  it("maps a 409 onto the reference field", async () => {
    const problem = { status: 409, title: "Conflict", errors: [{ field: "ref", code: "duplicate", message: "That reference is already in use." }] };
    vi.spyOn(client, "createPerson").mockRejectedValue(new ApiError(409, "Conflict", problem));
    const { user } = openCreate();

    await user.type(screen.getByLabelText("Reference"), "p01");
    await user.type(screen.getByLabelText("Name"), "Duplicate");
    await user.click(screen.getByRole("button", { name: "Add to roster" }));

    expect(await screen.findByText("That reference is already in use.")).toBeTruthy();
  });

  it("shows a 403's message honestly rather than a generic failure", async () => {
    const problem = { status: 403, title: "Forbidden", detail: "You don't hold team.person.write for this organization." };
    vi.spyOn(client, "createPerson").mockRejectedValue(new ApiError(403, "Forbidden", problem));
    const { user } = openCreate();

    await user.type(screen.getByLabelText("Reference"), "p20");
    await user.type(screen.getByLabelText("Name"), "New Hire");
    await user.click(screen.getByRole("button", { name: "Add to roster" }));

    expect(await screen.findByText("You don't hold team.person.write for this organization.")).toBeTruthy();
  });

  it("does not show the reference field when editing — ref is immutable", () => {
    const existing: Person = { id: "p01", uuid: "uuid-1", name: "Nadia Rahman", role: "Lead", location: "Bengaluru", capacity_hours: 20 };
    render(<PersonFormDialog person={existing} onClose={vi.fn()} onSaved={vi.fn()} />);

    expect(screen.queryByLabelText("Reference")).toBeNull();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeTruthy();
  });
});
