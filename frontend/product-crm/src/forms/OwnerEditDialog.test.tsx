import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Owner } from "@/api/types";
import { OwnerEditDialog } from "./OwnerEditDialog";

const OWNER: Owner = {
  id: "own-1",
  full_name: "Sofia Marchetti",
  email: "sofia@example.com",
  role: "AE",
  is_active: true,
};

afterEach(() => {
  vi.restoreAllMocks();
});

function open(onSaved = vi.fn()) {
  render(<OwnerEditDialog owner={OWNER} onClose={vi.fn()} onSaved={onSaved} />);
  return { user: userEvent.setup(), onSaved };
}

describe("OwnerEditDialog", () => {
  it("prefills every field from the owner being edited", () => {
    open();
    expect((screen.getByLabelText(/full name/i) as HTMLInputElement).value).toBe("Sofia Marchetti");
    expect((screen.getByLabelText(/^email$/i) as HTMLInputElement).value).toBe("sofia@example.com");
    expect((screen.getByLabelText(/role/i) as HTMLInputElement).value).toBe("AE");
  });

  it("refuses a blank name without calling the API", async () => {
    const spy = vi.spyOn(client, "updateOwner");
    const { user } = open();

    await user.clear(screen.getByLabelText(/full name/i));
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    expect(await screen.findByText(/enter their full name/i)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("sends the edited fields as a patch to the owner's id", async () => {
    const updated = { ...OWNER, role: "Senior AE" };
    vi.spyOn(client, "updateOwner").mockResolvedValue(updated);
    const { user, onSaved } = open();

    await user.clear(screen.getByLabelText(/role/i));
    await user.type(screen.getByLabelText(/role/i), "Senior AE");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() =>
      expect(client.updateOwner).toHaveBeenCalledWith("own-1", {
        full_name: "Sofia Marchetti",
        email: "sofia@example.com",
        role: "Senior AE",
      }),
    );
    expect(onSaved).toHaveBeenCalledWith(updated);
  });

  it("maps a 409 duplicate-email failure onto the dialog", async () => {
    vi.spyOn(client, "updateOwner").mockRejectedValue(
      new ApiError(409, "An owner with that email already exists.", {
        title: "Conflict",
        status: 409,
        detail: "An owner with that email already exists.",
      }),
    );
    const { user } = open();

    await user.click(screen.getByRole("button", { name: /save changes/i }));

    expect(await screen.findByText(/an owner with that email already exists/i)).toBeTruthy();
  });
});
