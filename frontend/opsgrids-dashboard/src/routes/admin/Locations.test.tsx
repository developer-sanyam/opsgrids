import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import { LocationsPage } from "./Locations";

vi.mock("@/api", () => ({
  listLocations: vi.fn(),
  createLocation: vi.fn(),
  updateLocation: vi.fn(),
}));

import * as api from "@/api";

const listLocations = vi.mocked(api.listLocations);
const createLocation = vi.mocked(api.createLocation);

const bengaluru = { id: "loc-1", name: "Bengaluru HQ", code: "BLR", timezone: "Asia/Kolkata", country: "IN", address: null, is_active: true };

function renderPage() {
  return render(
    <PermissionProvider grants={["core.org.location.write:ORG"]}>
      <LocationsPage />
    </PermissionProvider>,
  );
}

async function openCreateDialog(user: ReturnType<typeof userEvent.setup>) {
  // The empty state also offers a "New location" action once the list has loaded with no rows —
  // the header's own button is the first one in document order.
  await waitFor(() => expect(screen.getAllByRole("button", { name: "New location" }).length).toBeGreaterThan(0));
  await user.click(screen.getAllByRole("button", { name: "New location" })[0] as HTMLElement);
  await screen.findByRole("dialog", { name: "New location" });
}

describe("LocationsPage — the create form", () => {
  it("refuses an empty name before calling the server", async () => {
    listLocations.mockResolvedValue([]);
    const user = userEvent.setup();
    renderPage();
    await openCreateDialog(user);

    await user.type(screen.getByLabelText("Code"), "BLR");
    await user.click(screen.getByRole("button", { name: "Create location" }));

    expect(await screen.findByText("Give the location a name.")).toBeTruthy();
    expect(createLocation).not.toHaveBeenCalled();
  });

  it("disables the submit button while pending and never double-submits", async () => {
    listLocations.mockResolvedValue([]);
    let resolveCreate: (value: typeof bengaluru) => void = () => undefined;
    createLocation.mockReturnValue(new Promise((resolve) => (resolveCreate = resolve)));
    const user = userEvent.setup();
    renderPage();
    await openCreateDialog(user);

    await user.type(screen.getByLabelText("Name"), "Bengaluru HQ");
    await user.type(screen.getByLabelText("Code"), "BLR");
    const submit = screen.getByRole("button", { name: "Create location" });
    await user.click(submit);
    await user.click(submit); // a second click while pending must not fire a second request

    await waitFor(() => expect(screen.getByRole("button", { name: "Creating…" })).toBeTruthy());
    expect(createLocation).toHaveBeenCalledTimes(1);

    resolveCreate(bengaluru);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("closes the dialog and reloads the list on success", async () => {
    listLocations.mockResolvedValueOnce([]).mockResolvedValueOnce([bengaluru]);
    createLocation.mockResolvedValue(bengaluru);
    const user = userEvent.setup();
    renderPage();
    await openCreateDialog(user);

    await user.type(screen.getByLabelText("Name"), "Bengaluru HQ");
    await user.type(screen.getByLabelText("Code"), "BLR");
    await user.click(screen.getByRole("button", { name: "Create location" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(listLocations).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("Bengaluru HQ")).toBeTruthy();
  });

  it("maps a 422 to the field it names, and keeps the dialog open with what was typed", async () => {
    listLocations.mockResolvedValue([]);
    createLocation.mockRejectedValue(
      new ApiError(422, "That code is already used.", {
        status: 422,
        detail: "That code is already used.",
        errors: [{ field: "code", code: "conflict", message: "That code is already used." }],
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await openCreateDialog(user);

    await user.type(screen.getByLabelText("Name"), "Bengaluru HQ");
    await user.type(screen.getByLabelText("Code"), "BLR");
    await user.click(screen.getByRole("button", { name: "Create location" }));

    expect(await screen.findByText("That code is already used.")).toBeTruthy();
    expect(screen.getByLabelText("Name")).toHaveProperty("value", "Bengaluru HQ");
  });

  it("shows a 403's own sentence as the form's general error, not a field error", async () => {
    listLocations.mockResolvedValue([]);
    createLocation.mockRejectedValue(
      new ApiError(403, "You don't have permission to do this.", {
        status: 403,
        detail: "You don't have permission to do this.",
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await openCreateDialog(user);

    await user.type(screen.getByLabelText("Name"), "Bengaluru HQ");
    await user.type(screen.getByLabelText("Code"), "BLR");
    await user.click(screen.getByRole("button", { name: "Create location" }));

    expect(await screen.findByText("You don't have permission to do this.")).toBeTruthy();
  });
});
