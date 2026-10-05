/**
 * A value pasted into another system: shown whole, copied exactly, and — when the clipboard is
 * refused — an honest sentence instead of a button that silently did nothing.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Announcer } from "./Announcer";
import { CopyField } from "./CopyField";

const URL = "https://app.opsgrids.example/api/v1/webhooks/collections?account=abc";

function renderField() {
  return render(
    <Announcer>
      <CopyField label="Webhook URL" value={URL} helper="Paste it in the dashboard." />
    </Announcer>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CopyField", () => {
  it("shows the whole value as text, with its label and helper", () => {
    renderField();
    expect(screen.getByText(URL).tagName).toBe("CODE");
    expect(screen.getByText("Webhook URL")).toBeDefined();
    expect(screen.getByText("Paste it in the dashboard.")).toBeDefined();
  });

  it("copies the exact value and says so", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    renderField();
    await user.click(screen.getByRole("button", { name: "Copy Webhook URL" }));
    expect(writeText).toHaveBeenCalledWith(URL);
    await waitFor(() => expect(screen.getByText("Webhook URL copied.")).toBeDefined());
    // The accessible name says it too, not only the text a sighted user sees.
    expect(screen.getByRole("button", { name: "Copied Webhook URL" }).textContent).toContain("Copied");
  });

  it("says so on screen when the clipboard refuses, with no announcer mounted at all", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    render(<CopyField label="Webhook URL" value={URL} />);
    await user.click(screen.getByRole("button", { name: "Copy Webhook URL" }));
    const status = await screen.findByRole("status");
    expect(status.textContent).toBe("Couldn't copy the webhook url. Select it and copy it yourself.");
    expect(screen.getByRole("button", { name: "Copy Webhook URL" }).textContent).toContain("Copy");
  });

  it("says how to copy it by hand when the clipboard refuses", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    renderField();
    await user.click(screen.getByRole("button", { name: "Copy Webhook URL" }));
    await waitFor(() =>
      expect(screen.getByText("Couldn't copy the webhook url. Select it and copy it yourself.")).toBeDefined(),
    );
  });
});
