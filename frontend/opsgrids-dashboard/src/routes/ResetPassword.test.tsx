import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { MIN_PASSWORD_LENGTH } from "@opsgrids/client";

import { ResetPasswordPage } from "./ResetPassword";

vi.mock("@/api", () => ({
  setPasswordFromLink: vi.fn().mockResolvedValue(undefined),
}));

const GOOD = "a-long-enough-password";

function renderAt(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/reset-password${search}`]}>
      <ResetPasswordPage />
    </MemoryRouter>,
  );
}

async function submit(user: ReturnType<typeof userEvent.setup>, password: string) {
  await user.type(screen.getByLabelText("New password"), password);
  await user.type(screen.getByLabelText("Once more"), password);
  await user.click(screen.getByRole("button", { name: "Set password" }));
}

describe("ResetPasswordPage", () => {
  it("states the same minimum the server enforces", () => {
    renderAt("?token=abc");
    expect(screen.getByText(`At least ${MIN_PASSWORD_LENGTH} characters.`)).toBeTruthy();
  });

  it("tells a returning user their other sessions were signed out", async () => {
    const user = userEvent.setup();
    renderAt("?token=abc");
    await submit(user, GOOD);
    await waitFor(() => expect(screen.getByText("Your password is set")).toBeTruthy());
    expect(screen.getByText("Every other session on this account has been signed out.")).toBeTruthy();
  });

  it("welcomes a first-time user and does not mention other sessions", async () => {
    const user = userEvent.setup();
    renderAt("?token=abc&welcome=1");
    expect(screen.getByRole("heading", { name: "Welcome to OpsGrids" })).toBeTruthy();
    await submit(user, GOOD);
    await waitFor(() => expect(screen.getByText("You're all set")).toBeTruthy());
    expect(screen.queryByText(/other session/i)).toBeNull();
  });
});
