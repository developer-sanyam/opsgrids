/**
 * `RedirectToCentralLogin` is status-aware on purpose (see its own doc comment) — this is what
 * closes the SSO redirect loop (security review, fix round 1, B1): a redirect fired regardless
 * of status, built from `window.location.href` rather than the page the user actually wanted,
 * bounced a signed-in dashboard straight back to whichever product `/login` sent it there, even
 * when that product's own silent refresh had just succeeded.
 *
 * `AuthContext` is supplied directly (the same pattern `permissions/grants.test.tsx` uses)
 * rather than a real `<AuthProvider>` plus mocked `fetch`: this file is about what the component
 * renders and calls for a given *status*, not about how the provider arrives at one — that is
 * `silentRefresh.test.tsx` and `ssoLoop.test.tsx`'s job.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AuthContext } from "./context";
import type { AuthContextValue, AuthStatus } from "./context";
import { RedirectToCentralLogin } from "./RedirectToCentralLogin";

function authValue(status: AuthStatus): AuthContextValue {
  return {
    status,
    user: null,
    login: () => Promise.reject(new Error("not used")),
    verifyMfa: () => Promise.reject(new Error("not used")),
    selectOrg: () => Promise.reject(new Error("not used")),
    logout: () => Promise.resolve(),
  };
}

function renderAt(status: AuthStatus, from?: string) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/login", state: from ? { from } : null }]}>
      <AuthContext value={authValue(status)}>
        <Routes>
          <Route
            path="/login"
            element={
              <RedirectToCentralLogin
                authOrigin="https://app.opsgrids.com/"
                logo={<span data-testid="mark" />}
                appName="People"
              />
            }
          />
          <Route path="/" element={<div>app home</div>} />
          <Route path="/employees/42" element={<div>employee 42</div>} />
        </Routes>
      </AuthContext>
    </MemoryRouter>,
  );
}

const realLocation = window.location;
let assign: ReturnType<typeof vi.fn>;
let reload: ReturnType<typeof vi.fn>;

beforeEach(() => {
  assign = vi.fn();
  reload = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...realLocation, assign, reload },
  });
  sessionStorage.clear();
});

afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
  sessionStorage.clear();
});

describe("while the boot check is still running", () => {
  it("holds, showing the product's mark and a status message — never redirects", () => {
    renderAt("loading", "/employees/42");

    expect(screen.getByTestId("mark")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Taking you to sign in…");
    expect(assign).not.toHaveBeenCalled();
  });
});

describe("once the boot check finds no session", () => {
  it("sends the browser to the dashboard with the wanted page as next, not window.location.href", () => {
    renderAt("unauthenticated", "/employees/42");

    expect(assign).toHaveBeenCalledWith(
      "https://app.opsgrids.com/login?next=" +
        encodeURIComponent("http://localhost:3000/employees/42"),
    );
  });

  it("falls back to this app's home when there was no wanted page", () => {
    renderAt("unauthenticated");

    expect(assign).toHaveBeenCalledWith(
      "https://app.opsgrids.com/login?next=" + encodeURIComponent("http://localhost:3000/"),
    );
  });

  it("never sends /login itself as next, even if that is somehow the recorded wanted page", () => {
    renderAt("unauthenticated", "/login");

    expect(assign).toHaveBeenCalledWith(
      "https://app.opsgrids.com/login?next=" + encodeURIComponent("http://localhost:3000/"),
    );
  });
});

describe("once the boot check finds a session after all (the silent refresh succeeded)", () => {
  it("navigates to the wanted page inside this app — never window.location.assign", async () => {
    renderAt("authenticated", "/employees/42");

    await waitFor(() => expect(screen.getByText("employee 42")).toBeTruthy());
    expect(assign).not.toHaveBeenCalled();
  });

  it("navigates to this app's home when there was no wanted page", async () => {
    renderAt("authenticated");

    await waitFor(() => expect(screen.getByText("app home")).toBeTruthy());
    expect(assign).not.toHaveBeenCalled();
  });
});

describe("when the boot check itself could not complete", () => {
  it("shows a retry screen instead of redirecting anywhere", () => {
    renderAt("error", "/employees/42");

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(assign).not.toHaveBeenCalled();
  });

  it("retrying reloads the page", async () => {
    const user = (await import("@testing-library/user-event")).default.setup();
    renderAt("error");

    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});

describe("the loop breaker", () => {
  it("blocks a third hand-off within a minute and offers a way out", async () => {
    sessionStorage.setItem("opsgrids.sso_handoff_at", JSON.stringify([Date.now(), Date.now()]));
    const user = (await import("@testing-library/user-event")).default.setup();

    renderAt("unauthenticated", "/employees/42");

    expect(assign).not.toHaveBeenCalled();
    expect(screen.getByText("Couldn't sign you in to People.")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Open dashboard" }));
    expect(assign).toHaveBeenCalledWith("https://app.opsgrids.com");
  });

  it("an old hand-off outside the window does not count", () => {
    sessionStorage.setItem(
      "opsgrids.sso_handoff_at",
      JSON.stringify([Date.now() - 120_000, Date.now() - 90_000]),
    );

    renderAt("unauthenticated", "/employees/42");

    expect(assign).toHaveBeenCalledWith(
      "https://app.opsgrids.com/login?next=" +
        encodeURIComponent("http://localhost:3000/employees/42"),
    );
  });

  it("trying again clears the count and reloads", async () => {
    sessionStorage.setItem("opsgrids.sso_handoff_at", JSON.stringify([Date.now(), Date.now()]));
    const user = (await import("@testing-library/user-event")).default.setup();

    renderAt("unauthenticated");
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(sessionStorage.getItem("opsgrids.sso_handoff_at")).toBeNull();
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
