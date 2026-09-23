import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import { OwnerFormDialog } from "./OwnerFormDialog";

const CREATED = { id: "own-new", full_name: "Grace Adeyemi", email: "grace@example.com", role: "AE", is_active: true };

afterEach(() => {
  vi.restoreAllMocks();
});

function open(onSaved = vi.fn()) {
  render(<OwnerFormDialog onClose={vi.fn()} onSaved={onSaved} />);
  return { user: userEvent.setup(), onSaved };
}

describe("OwnerFormDialog", () => {
  it("shows a validation message and never calls the API when the name is blank", async () => {
    const spy = vi.spyOn(client, "createOwner");
    const { user } = open();

    await user.click(screen.getByRole("button", { name: /add team member/i }));

    expect(await screen.findByText(/enter their full name/i)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("rejects a malformed email before the request goes out", async () => {
    const spy = vi.spyOn(client, "createOwner");
    const { user } = open();

    await user.type(screen.getByLabelText(/full name/i), "Grace Adeyemi");
    await user.type(screen.getByLabelText(/^email$/i), "not-an-email");
    await user.click(screen.getByRole("button", { name: /add team member/i }));

    expect(await screen.findByText(/enter a valid email/i)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("disables submit while pending and blocks a second submission", async () => {
    let resolve!: (value: typeof CREATED) => void;
    vi.spyOn(client, "createOwner").mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const { user } = open();

    await user.type(screen.getByLabelText(/full name/i), "Grace Adeyemi");
    await user.type(screen.getByLabelText(/^email$/i), "grace@example.com");
    const submit = screen.getByRole("button", { name: /add team member/i });
    await user.click(submit);

    const pendingButton = await screen.findByRole("button", { name: /adding/i });
    expect(pendingButton).toHaveProperty("disabled", true);
    await user.click(pendingButton); // ignored — one write in flight

    resolve(CREATED);
    await waitFor(() => expect(client.createOwner).toHaveBeenCalledTimes(1));
  });

  it("on success calls onSaved with the created owner", async () => {
    vi.spyOn(client, "createOwner").mockResolvedValue(CREATED);
    const { user, onSaved } = open();

    await user.type(screen.getByLabelText(/full name/i), "Grace Adeyemi");
    await user.type(screen.getByLabelText(/^email$/i), "grace@example.com");
    await user.type(screen.getByLabelText(/role/i), "AE");
    await user.click(screen.getByRole("button", { name: /add team member/i }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED));
    expect(client.createOwner).toHaveBeenCalledWith({
      full_name: "Grace Adeyemi",
      email: "grace@example.com",
      role: "AE",
    });
  });

  it("maps a 409 duplicate-email failure onto the dialog, keeping the typed data", async () => {
    vi.spyOn(client, "createOwner").mockRejectedValue(
      new ApiError(409, "A team member with that email already exists.", {
        title: "Conflict",
        status: 409,
        detail: "A team member with that email already exists.",
      }),
    );
    const { user } = open();

    await user.type(screen.getByLabelText(/full name/i), "Grace Adeyemi");
    await user.type(screen.getByLabelText(/^email$/i), "grace@example.com");
    await user.click(screen.getByRole("button", { name: /add team member/i }));

    expect(await screen.findByText(/a team member with that email already exists/i)).toBeTruthy();
    // The typed values survive the failed submit.
    expect((screen.getByLabelText(/full name/i) as HTMLInputElement).value).toBe("Grace Adeyemi");
    expect((screen.getByLabelText(/^email$/i) as HTMLInputElement).value).toBe("grace@example.com");
  });
});
