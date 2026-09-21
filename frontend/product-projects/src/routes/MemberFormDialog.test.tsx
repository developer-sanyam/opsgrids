import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { MemberOut } from "@/api/types";
import { MemberFormDialog } from "./MemberFormDialog";

const CREATED: MemberOut = { id: "p-11", name: "New Hire", role: "", directory_id: null };

afterEach(() => {
  vi.restoreAllMocks();
});

function openCreate(onSaved = vi.fn(), onClose = vi.fn()) {
  render(<MemberFormDialog member={null} onClose={onClose} onSaved={onSaved} />);
  return { user: userEvent.setup(), onSaved, onClose };
}

describe("MemberFormDialog", () => {
  it("refuses to submit without a name", async () => {
    const spy = vi.spyOn(client, "createMember");
    const { user } = openCreate();

    await user.type(screen.getByLabelText("Reference"), "p-11");
    await user.click(screen.getByRole("button", { name: "Add member" }));

    expect(await screen.findByText("Give this person a name.")).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("disables submit and blocks a double submit while pending", async () => {
    let resolveCreate: (value: MemberOut) => void = () => undefined;
    vi.spyOn(client, "createMember").mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve;
      }),
    );
    const { user } = openCreate();

    await user.type(screen.getByLabelText("Reference"), "p-11");
    await user.type(screen.getByLabelText("Name"), "New Hire");
    const submit = screen.getByRole("button", { name: "Add member" });
    await user.click(submit);
    await user.click(submit);

    expect(screen.getByRole("button", { name: "Adding…" })).toBeTruthy();
    expect(client.createMember).toHaveBeenCalledTimes(1);
    resolveCreate(CREATED);
  });

  it("closes and reports the created member on success, and reloads via the screen's onSaved", async () => {
    vi.spyOn(client, "createMember").mockResolvedValue(CREATED);
    const { user, onSaved, onClose } = openCreate();

    await user.type(screen.getByLabelText("Reference"), "p-11");
    await user.type(screen.getByLabelText("Name"), "New Hire");
    await user.click(screen.getByRole("button", { name: "Add member" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED, "New Hire added to the team."));
    expect(onClose).toHaveBeenCalled();
  });

  it("preserves the typed input after a failed submit", async () => {
    vi.spyOn(client, "createMember").mockRejectedValue(new ApiError(500, "boom", null));
    const { user } = openCreate();

    await user.type(screen.getByLabelText("Reference"), "p-11");
    await user.type(screen.getByLabelText("Name"), "New Hire");
    await user.click(screen.getByRole("button", { name: "Add member" }));

    await waitFor(() => expect(screen.getByLabelText("Name")).toHaveProperty("value", "New Hire"));
  });

  it("a 409 duplicate ref maps onto the reference field", async () => {
    const problem = {
      status: 409,
      title: "Conflict",
      errors: [{ field: "ref", code: "duplicate", message: "That reference is already in use." }],
    };
    vi.spyOn(client, "createMember").mockRejectedValue(new ApiError(409, "Conflict", problem));
    const { user } = openCreate();

    await user.type(screen.getByLabelText("Reference"), "p-01");
    await user.type(screen.getByLabelText("Name"), "Duplicate");
    await user.click(screen.getByRole("button", { name: "Add member" }));

    expect(await screen.findByText("That reference is already in use.")).toBeTruthy();
  });

  it("shows a 403's own message rather than a generic failure", async () => {
    const problem = {
      status: 403,
      title: "Forbidden",
      detail: "You don't hold projects.team.member.write for this organization.",
    };
    vi.spyOn(client, "createMember").mockRejectedValue(new ApiError(403, "Forbidden", problem));
    const { user } = openCreate();

    await user.type(screen.getByLabelText("Reference"), "p-11");
    await user.type(screen.getByLabelText("Name"), "New Hire");
    await user.click(screen.getByRole("button", { name: "Add member" }));

    expect(
      await screen.findByText("You don't hold projects.team.member.write for this organization."),
    ).toBeTruthy();
  });

  it("shows the reference read-only when editing — ref is immutable", () => {
    const existing: MemberOut = { id: "p-01", name: "Aarav Sharma", role: "Delivery Lead", directory_id: null };
    render(<MemberFormDialog member={existing} onClose={vi.fn()} onSaved={vi.fn()} />);

    const reference = screen.getByLabelText("Reference");
    expect(reference).toHaveProperty("value", "p-01");
    expect(reference).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "Save changes" })).toBeTruthy();
  });
});
