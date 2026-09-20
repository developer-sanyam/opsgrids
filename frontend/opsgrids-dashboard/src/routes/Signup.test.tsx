import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import { suggestSlug } from "@/lib/slug";

import { SignupPage } from "./Signup";

vi.mock("@/api", () => ({
  signup: vi.fn(),
  requestPasswordLink: vi.fn(),
}));

import * as api from "@/api";

const signup = vi.mocked(api.signup);
const requestPasswordLink = vi.mocked(api.requestPasswordLink);

function renderPage() {
  return render(
    <MemoryRouter>
      <SignupPage />
    </MemoryRouter>,
  );
}

async function fill(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Organisation name"), "Greenleaf Organics");
  await user.type(screen.getByLabelText("Your name"), "Asha Verma");
  await user.type(screen.getByLabelText("Work email"), "asha@greenleaf.example");
}

describe("suggestSlug", () => {
  it("turns a name into the shape the service accepts", () => {
    expect(suggestSlug("Greenleaf Organics")).toBe("greenleaf-organics");
    expect(suggestSlug("  Ünïcode & Sons!! ")).toBe("unicode-sons");
    expect(suggestSlug("---")).toBe("");
  });
});

describe("SignupPage", () => {
  it("carries no password field — the link comes by email (ADR-0013 §3)", () => {
    renderPage();
    expect(screen.queryByLabelText(/password/i)).toBeNull();
  });

  it("creates the organisation, requests the link, and ends on the inbox", async () => {
    const user = userEvent.setup();
    signup.mockResolvedValue({
      organization_id: "org-1",
      slug: "greenleaf-organics",
      email: "asha@greenleaf.example",
      next_step: "Check your inbox for the link that sets your password.",
    });
    requestPasswordLink.mockResolvedValue(undefined);
    renderPage();
    await fill(user);
    // The address was suggested from the name and not touched.
    expect(screen.getByLabelText("Address")).toHaveProperty("value", "greenleaf-organics");

    await user.click(screen.getByRole("button", { name: "Create organisation" }));

    await waitFor(() => expect(screen.getByRole("heading", { name: "Check your inbox" })).toBeTruthy());
    expect(signup).toHaveBeenCalledWith({
      organization_name: "Greenleaf Organics",
      slug: "greenleaf-organics",
      full_name: "Asha Verma",
      email: "asha@greenleaf.example",
    });
    expect(requestPasswordLink).toHaveBeenCalledWith("asha@greenleaf.example");
  });

  it("shows the service's sentence on a 409 and stays on the form", async () => {
    const user = userEvent.setup();
    signup.mockRejectedValue(
      new ApiError(409, "That email already has an account. Sign in instead.", {
        status: 409,
        detail: "That email already has an account. Sign in instead.",
      }),
    );
    renderPage();
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Create organisation" }));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Sign in instead"));
    expect(requestPasswordLink).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Create organisation" })).toBeTruthy();
  });

  it("refuses a malformed address before calling anything", async () => {
    const user = userEvent.setup();
    renderPage();
    await fill(user);
    const address = screen.getByLabelText("Address");
    await user.clear(address);
    await user.type(address, "-bad");
    await user.click(screen.getByRole("button", { name: "Create organisation" }));

    expect(screen.getByRole("alert").textContent).toContain("lowercase letters");
    expect(signup).not.toHaveBeenCalled();
  });
});
