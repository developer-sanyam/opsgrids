import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as authApi from "./api";
import { AuthProvider } from "./AuthProvider";
import { useAuth } from "./context";
import type { OperatorMe, OperatorRole } from "./types";

/**
 * `hasRole` is the one thing every UI permission hint in this app calls — pinned here against the
 * real, shipped implementation (not a mock of it) so a typo in `OperatorRole` or in `hasRole`'s
 * comparison shows up here rather than only as a control that quietly never appears for a real
 * `owner` in production. Runs the live branch (this suite pins `VITE_USE_FIXTURES=false`, see
 * `vitest.config.ts`), so `operator_roles` is whatever `authApi.me` is mocked to return — not the
 * fixtures-mode stand-in, which is exercised separately below.
 */

function operatorWith(roles: OperatorRole[]): OperatorMe {
  return {
    operator_id: "op_test",
    email: "b@opsgrids.com",
    full_name: "B Test",
    operator_roles: roles,
    amr: ["pwd"],
    mfa_enabled: false,
  };
}

const LOGIN_COMPLETE = {
  status: "complete" as const,
  access_token: "tok",
  token_type: "Bearer" as const,
  expires_in: 900,
  operator_id: "op_test",
};

function RoleProbe() {
  const { operator, hasRole, login } = useAuth();
  return (
    <div>
      <button type="button" onClick={() => void login("b@opsgrids.com", "pw")}>
        Sign in
      </button>
      {operator ? (
        <dl>
          <dt>owner</dt>
          <dd>{String(hasRole("owner"))}</dd>
          <dt>billing_ops</dt>
          <dd>{String(hasRole("billing_ops"))}</dd>
          <dt>support</dt>
          <dd>{String(hasRole("support"))}</dd>
        </dl>
      ) : null}
    </div>
  );
}

beforeEach(() => {
  sessionStorage.clear();
});

describe("hasRole against the live operator roles", () => {
  it("recognises billing_ops and does not also grant owner or support", async () => {
    vi.spyOn(authApi, "login").mockResolvedValue(LOGIN_COMPLETE);
    vi.spyOn(authApi, "me").mockResolvedValue(operatorWith(["billing_ops"]));

    render(
      <AuthProvider>
        <RoleProbe />
      </AuthProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("true")).toBeTruthy();
    const values = screen.getAllByRole("definition").map((el) => el.textContent);
    expect(values).toEqual(["false", "true", "false"]); // owner, billing_ops, support
  });

  it("recognises owner", async () => {
    vi.spyOn(authApi, "login").mockResolvedValue(LOGIN_COMPLETE);
    vi.spyOn(authApi, "me").mockResolvedValue(operatorWith(["owner"]));

    render(
      <AuthProvider>
        <RoleProbe />
      </AuthProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await screen.findByText("true");
    const values = screen.getAllByRole("definition").map((el) => el.textContent);
    expect(values).toEqual(["true", "false", "false"]);
  });

  it("recognises support, and grants nothing wider", async () => {
    vi.spyOn(authApi, "login").mockResolvedValue(LOGIN_COMPLETE);
    vi.spyOn(authApi, "me").mockResolvedValue(operatorWith(["support"]));

    render(
      <AuthProvider>
        <RoleProbe />
      </AuthProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await screen.findByText("true");
    const values = screen.getAllByRole("definition").map((el) => el.textContent);
    expect(values).toEqual(["false", "false", "true"]);
  });

  it("denies every role for an operator who holds none", async () => {
    vi.spyOn(authApi, "login").mockResolvedValue(LOGIN_COMPLETE);
    vi.spyOn(authApi, "me").mockResolvedValue(operatorWith([]));

    render(
      <AuthProvider>
        <RoleProbe />
      </AuthProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    const values = await screen.findAllByRole("definition");
    expect(values.map((el) => el.textContent)).toEqual(["false", "false", "false"]);
  });
});
