import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Audience } from "@/api/types";
import { AudienceFormDialog } from "./AudienceFormDialog";

const CREATED: Audience = {
  id: "aud-1",
  name: "Enterprise India",
  size: 4210,
  criteria: "",
  created_at: "2026-08-01",
};

afterEach(() => {
  vi.restoreAllMocks();
});

function openCreate(onSaved = vi.fn(), onClose = vi.fn()) {
  render(<AudienceFormDialog audience={null} onClose={onClose} onSaved={onSaved} />);
  return { user: userEvent.setup(), onSaved, onClose };
}

async function fillRequired(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Name"), "Enterprise India");
  const sizeField = screen.getByLabelText("Reachable people");
  await user.clear(sizeField);
  await user.type(sizeField, "4210");
}

describe("AudienceFormDialog", () => {
  it("refuses an empty name, client-side, before any request", async () => {
    const spy = vi.spyOn(client, "createAudience");
    const { user } = openCreate();

    await user.click(screen.getByRole("button", { name: "Create audience" }));

    expect(await screen.findByText("Give the segment a name.")).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("defaults 'Defined on' to today so the required date is never blank", () => {
    render(<AudienceFormDialog audience={null} onClose={vi.fn()} onSaved={vi.fn()} />);
    const field = screen.getByLabelText("Defined on") as HTMLInputElement;
    expect(field.value).not.toBe("");
  });

  it("disables submit and blocks a double submit while pending", async () => {
    let resolveCreate: (value: Audience) => void = () => undefined;
    vi.spyOn(client, "createAudience").mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve;
      }),
    );
    const { user } = openCreate();

    await fillRequired(user);
    const submit = screen.getByRole("button", { name: "Create audience" });
    await user.click(submit);
    await user.click(submit);

    expect(screen.getByRole("button", { name: "Creating…" })).toBeTruthy();
    expect(client.createAudience).toHaveBeenCalledTimes(1);
    resolveCreate(CREATED);
  });

  it("creates, closes and reports the saved audience on success", async () => {
    vi.spyOn(client, "createAudience").mockResolvedValue(CREATED);
    const { user, onSaved, onClose } = openCreate();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Create audience" }));

    await waitFor(() =>
      expect(client.createAudience).toHaveBeenCalledWith(
        expect.objectContaining({ name: "Enterprise India", size: 4210 }),
      ),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED));
    expect(onClose).not.toHaveBeenCalled(); // the screen closes it from onSaved, not the dialog itself
  });

  it("a 422 field error from the server lands on its field and keeps the entered value", async () => {
    const problem = {
      status: 422,
      title: "Validation failed",
      detail: "Request payload failed validation",
      errors: [{ field: "size", code: "greater_than_equal", message: "Input should be >= 0" }],
    };
    vi.spyOn(client, "createAudience").mockRejectedValue(new ApiError(422, "Validation failed", problem));
    const { user } = openCreate();

    // A value that passes this form's own client-side check (a non-negative integer) so the
    // mocked server rejection is what the field ends up showing.
    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Create audience" }));

    expect(await screen.findByText("Input should be >= 0")).toBeTruthy();
    expect(screen.getByLabelText("Name")).toHaveProperty("value", "Enterprise India");
  });

  it("a duplicate name (409) shows the server's detail at the top, not on a field", async () => {
    const problem = { status: 409, title: "Conflict", detail: "An audience named 'Enterprise India' already exists" };
    vi.spyOn(client, "createAudience").mockRejectedValue(new ApiError(409, "Conflict", problem));
    const { user } = openCreate();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Create audience" }));

    expect(await screen.findByText("An audience named 'Enterprise India' already exists")).toBeTruthy();
  });

  it("a 403 (no write grant) shows the role message — the button is hidden by `Can`, not this dialog", async () => {
    const problem = {
      status: 403,
      title: "Not permitted",
      detail: "You need the Marketing administrator role to do this.",
    };
    vi.spyOn(client, "createAudience").mockRejectedValue(new ApiError(403, "Not permitted", problem));
    const { user } = openCreate();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Create audience" }));

    expect(await screen.findByText("You need the Marketing administrator role to do this.")).toBeTruthy();
  });

  it("pre-fills the fields in edit mode and submits a patch", async () => {
    vi.spyOn(client, "updateAudience").mockResolvedValue({ ...CREATED, name: "Renamed" });
    const user = userEvent.setup();
    const onSaved = vi.fn();
    render(<AudienceFormDialog audience={CREATED} onClose={vi.fn()} onSaved={onSaved} />);

    expect(screen.getByLabelText("Name")).toHaveProperty("value", "Enterprise India");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeTruthy();

    const nameField = screen.getByLabelText("Name");
    await user.clear(nameField);
    await user.type(nameField, "Renamed");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(client.updateAudience).toHaveBeenCalledWith(
        "aud-1",
        expect.objectContaining({ name: "Renamed" }),
      ),
    );
  });
});
