/**
 * The approval thresholds screen: anyone who can read stock sees them, only a holder of the
 * configure permission can change them, and what is sent is decimal strings (a blank clears one).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import { renderScreen } from "@/test-utils/renderScreen";
import { ApprovalSettingsPage } from "./ApprovalSettings";

const getControlSettings = vi.fn();
const putControlSettings = vi.fn();

vi.mock("@/api/movements", () => ({
  getControlSettings: (...args: unknown[]) => getControlSettings(...args),
  putControlSettings: (...args: unknown[]) => putControlSettings(...args),
}));

const CONFIGURE = "operations.inventory.control.configure:ORG";

const SETTINGS = {
  transfer_approval_threshold: "500",
  adjustment_approval_threshold: null,
  updated_at: "2026-10-04T09:00:00Z",
  updated_by: "Meera Nair",
};

beforeEach(() => {
  getControlSettings.mockReset();
  putControlSettings.mockReset();
  getControlSettings.mockResolvedValue(SETTINGS);
});

describe("ApprovalSettingsPage", () => {
  it("shows the current thresholds in words", async () => {
    renderScreen(<ApprovalSettingsPage />, []);
    expect(await screen.findByText("500 on any line")).toBeDefined();
    expect(screen.getByText("None — adjustments apply at once")).toBeDefined();
    expect(screen.getByText(/by Meera Nair/)).toBeDefined();
  });

  it("is read-only without the configure permission", async () => {
    renderScreen(<ApprovalSettingsPage />, []);
    const field = (await screen.findByLabelText(/Transfer approval threshold/)) as HTMLInputElement;
    expect(field.readOnly).toBe(true);
    expect(screen.queryByRole("button", { name: "Save thresholds" })).toBeNull();
    expect(screen.getByText(/You can see these thresholds but not change them/)).toBeDefined();
  });

  it("saves both thresholds as decimal strings, a blank one as null", async () => {
    putControlSettings.mockResolvedValue({ ...SETTINGS, adjustment_approval_threshold: "25.5", updated_at: "2026-10-05T09:00:00Z" });
    const user = userEvent.setup();
    renderScreen(<ApprovalSettingsPage />, [CONFIGURE]);

    await user.type(await screen.findByLabelText(/Adjustment approval threshold/), "25.50");
    await user.click(screen.getByRole("button", { name: "Save thresholds" }));

    await waitFor(() =>
      expect(putControlSettings).toHaveBeenCalledWith({
        transfer_approval_threshold: "500",
        adjustment_approval_threshold: "25.5",
      }),
    );
    expect(await screen.findByText("25.5 in one change")).toBeDefined();
  });

  it("clears a threshold when its field is emptied", async () => {
    putControlSettings.mockResolvedValue({ ...SETTINGS, transfer_approval_threshold: null });
    const user = userEvent.setup();
    renderScreen(<ApprovalSettingsPage />, [CONFIGURE]);

    await user.clear(await screen.findByLabelText(/Transfer approval threshold/));
    await user.click(screen.getByRole("button", { name: "Save thresholds" }));

    await waitFor(() =>
      expect(putControlSettings).toHaveBeenCalledWith({
        transfer_approval_threshold: null,
        adjustment_approval_threshold: null,
      }),
    );
  });

  it("puts a bad figure on its own field without calling the server", async () => {
    const user = userEvent.setup();
    renderScreen(<ApprovalSettingsPage />, [CONFIGURE]);

    const field = await screen.findByLabelText(/Transfer approval threshold/);
    await user.clear(field);
    await user.type(field, "abc");
    await user.click(screen.getByRole("button", { name: "Save thresholds" }));

    expect(await screen.findByText("Enter a number with at most 3 decimal places.")).toBeDefined();
    expect(putControlSettings).not.toHaveBeenCalled();
  });

  it("shows the server's sentence on the field it is about, and keeps what was typed", async () => {
    putControlSettings.mockRejectedValue(
      new ApiError(422, "x", {
        type: "https://errors.opsgrids.dev/validation",
        status: 422,
        detail: "A threshold must be greater than zero (clear it to set none)",
        errors: [
          {
            field: "adjustment_approval_threshold",
            code: "invalid_quantity",
            message: "A threshold must be greater than zero (clear it to set none)",
          },
        ],
      }),
    );
    const user = userEvent.setup();
    renderScreen(<ApprovalSettingsPage />, [CONFIGURE]);

    const field = (await screen.findByLabelText(/Adjustment approval threshold/)) as HTMLInputElement;
    await user.type(field, "7");
    await user.click(screen.getByRole("button", { name: "Save thresholds" }));

    expect(await screen.findByText("A threshold must be greater than zero (clear it to set none)")).toBeDefined();
    expect(field.value).toBe("7");
  });

  it("has an error state with a retry", async () => {
    getControlSettings.mockRejectedValueOnce(new ApiError(500, "The server fell over", null));
    const user = userEvent.setup();
    renderScreen(<ApprovalSettingsPage />, []);
    expect(await screen.findByText("Couldn't load the approval thresholds")).toBeDefined();
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText("500 on any line")).toBeDefined();
  });
});
