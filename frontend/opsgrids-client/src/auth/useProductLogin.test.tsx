import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../http";
import { AuthContext } from "./context";
import type { AuthContextValue } from "./context";
import type { LoginStep, OrganizationChoice } from "./types";
import { useProductLogin } from "./useProductLogin";

const ORG: OrganizationChoice = {
  organization_id: "org-a",
  slug: "acme",
  name: "Acme India",
  roles: ["admin"],
  role_names: ["Admin"],
};

function Probe() {
  const login = useProductLogin();
  return (
    <div>
      <p>step: {login.step}</p>
      <p>error: {login.error ?? "none"}</p>
      <p>submitting: {String(login.submitting)}</p>
      <p>orgs: {login.organizations.map((org) => org.name).join(",")}</p>
      <p>destination: {login.destination}</p>
      <button onClick={() => login.fill("a@b.co", "pw")}>fill</button>
      <button onClick={() => void login.submit()}>submit</button>
      <button onClick={() => void login.pickOrganization("org-a")}>pick</button>
      <p>
        creds: {login.email}/{login.password}
      </p>
    </div>
  );
}

function renderWith(auth: Partial<AuthContextValue>, from?: string) {
  const value = { status: "unauthenticated", ...auth } as AuthContextValue;
  return render(
    <AuthContext value={value}>
      <MemoryRouter initialEntries={[{ pathname: "/login", state: from === undefined ? null : { from } }]}>
        <Routes>
          <Route path="/login" element={<Probe />} />
          <Route path="*" element={<p>landed</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext>,
  );
}

describe("useProductLogin", () => {
  it("fills the fields and trims the email when it signs in, then goes where the person was headed", async () => {
    const login = vi.fn(async (): Promise<LoginStep> => ({ kind: "complete" }) as LoginStep);
    renderWith({ login }, "/deals");
    expect(screen.getByText("destination: /deals")).toBeTruthy();
    await userEvent.click(screen.getByText("fill"));
    expect(screen.getByText("creds: a@b.co/pw")).toBeTruthy();
    await userEvent.click(screen.getByText("submit"));
    await waitFor(() => expect(screen.getByText("landed")).toBeTruthy());
    expect(login).toHaveBeenCalledWith("a@b.co", "pw");
  });

  it("says the email or password is wrong for any 4xx, never which half", async () => {
    const login = vi.fn(async () => {
      throw new ApiError(401, "Unauthorized", { title: "Unauthorized", status: 401 });
    });
    renderWith({ login });
    await userEvent.click(screen.getByText("submit"));
    expect(await screen.findByText("error: That email or password is incorrect.")).toBeTruthy();
    expect(screen.getByText("submitting: false")).toBeTruthy();
  });

  it("reports a real fault plainly", async () => {
    const login = vi.fn(async () => {
      throw new Error("Network down");
    });
    renderWith({ login });
    await userEvent.click(screen.getByText("submit"));
    expect(await screen.findByText("error: Couldn't sign in. Network down")).toBeTruthy();
  });

  it("moves to the organization step when the person belongs to several", async () => {
    const login = vi.fn(async (): Promise<LoginStep> => ({ kind: "org_selection", organizations: [ORG] }) as LoginStep);
    renderWith({ login });
    await userEvent.click(screen.getByText("submit"));
    expect(await screen.findByText("step: organizations")).toBeTruthy();
    expect(screen.getByText("orgs: Acme India")).toBeTruthy();
  });

  it("explains a second factor it cannot take", async () => {
    const login = vi.fn(async (): Promise<LoginStep> => ({ kind: "mfa_required" }) as LoginStep);
    renderWith({ login });
    await userEvent.click(screen.getByText("submit"));
    expect(
      await screen.findByText("error: This account needs a second factor, which isn't supported here yet."),
    ).toBeTruthy();
  });

  it("finishes an organization pick, or says it could not", async () => {
    const selectOrg = vi.fn().mockRejectedValueOnce(new Error("Gateway timeout")).mockResolvedValueOnce(undefined);
    renderWith({ selectOrg });
    await userEvent.click(screen.getByText("pick"));
    expect(await screen.findByText("error: Couldn't finish signing in. Gateway timeout")).toBeTruthy();
    await userEvent.click(screen.getByText("pick"));
    await waitFor(() => expect(screen.getByText("landed")).toBeTruthy());
    expect(selectOrg).toHaveBeenCalledWith("org-a");
  });
});
