/**
 * "No employee record is linked to your account" -- the way out (ADR-0008, the sanctioned writer).
 *
 * The self-serve owner is a login with no employee. Create my employee record makes the record and
 * links this login to it through account's member API, then refreshes the session so the page
 * sees it. Everything is gated twice: the control by the caller's grants, the server by its own
 * ORG floor on both writes.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";

const createEmployee = vi.fn();
const setMemberEmployee = vi.fn();
const refreshAccessToken = vi.fn();

vi.mock("@/api/client", () => ({ createEmployee: (...a: unknown[]) => createEmployee(...a) }));
vi.mock("@/api/members", () => ({ setMemberEmployee: (...a: unknown[]) => setMemberEmployee(...a) }));
vi.mock("@opsgrids/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@opsgrids/client")>()),
  refreshAccessToken: () => refreshAccessToken(),
}));

import { LinkMyLoginCard } from "./LinkMyLoginCard";

const USER: MeOut = {
  user_id: "user-1",
  email: "asha@acme.example",
  full_name: "Asha Verma",
  organization_id: "org-1",
  membership_id: "mem-1",
  roles: ["org_owner"],
  role_names: ["Owner"],
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [],
};

const AUTH: AuthContextValue = {
  status: "authenticated",
  user: USER,
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

const ADMIN = ["core.identity.membership.write:ORG", "hrms.people.employee.write:ORG"];

function open(grants: string[], onLinked = vi.fn(), user: MeOut = USER) {
  render(
    <MemoryRouter>
      <PermissionProvider grants={grants}>
        <AuthContext value={{ ...AUTH, user }}>
          <LinkMyLoginCard onLinked={onLinked} />
        </AuthContext>
      </PermissionProvider>
    </MemoryRouter>,
  );
  return { user: userEvent.setup(), onLinked };
}

beforeEach(() => {
  createEmployee.mockReset();
  setMemberEmployee.mockReset();
  refreshAccessToken.mockReset();
  refreshAccessToken.mockResolvedValue({ outcome: "ok", expiresIn: 900 });
});

afterEach(() => vi.restoreAllMocks());

describe("an owner or administrator", () => {
  it("creates their employee record, links this login to it, refreshes, and tells the page", async () => {
    createEmployee.mockResolvedValue({ id: "emp-new" });
    setMemberEmployee.mockResolvedValue({});
    const { user, onLinked } = open(ADMIN);

    await user.click(screen.getByRole("button", { name: "Create my employee record" }));

    await waitFor(() => expect(onLinked).toHaveBeenCalledTimes(1));
    // The record is named as the login is, and no employee number is invented (the service numbers it).
    expect(createEmployee).toHaveBeenCalledWith({ full_name: "Asha Verma" });
    // The link is account's: this login's membership id, the new employee's id.
    expect(setMemberEmployee).toHaveBeenCalledWith("mem-1", "emp-new");
    expect(refreshAccessToken).toHaveBeenCalledTimes(1);
  });

  it("does not make a second employee when the link failed the first time", async () => {
    createEmployee.mockResolvedValue({ id: "emp-new" });
    setMemberEmployee
      .mockRejectedValueOnce(new ApiError(503, "Account is busy.", { title: "x", status: 503, detail: "Account is busy." }))
      .mockResolvedValueOnce({});
    const { user, onLinked } = open(ADMIN);

    await user.click(screen.getByRole("button", { name: "Create my employee record" }));
    expect(await screen.findByText(/Account is busy/)).toBeTruthy();
    expect(screen.getByText(/employee record was created; try again/i)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Create my employee record" }));
    await waitFor(() => expect(onLinked).toHaveBeenCalled());
    expect(createEmployee).toHaveBeenCalledTimes(1);
    expect(setMemberEmployee).toHaveBeenLastCalledWith("mem-1", "emp-new");
  });

  it("says plainly when the link is refused, and never tells the page it worked", async () => {
    createEmployee.mockResolvedValue({ id: "emp-new" });
    setMemberEmployee.mockRejectedValue(
      new ApiError(409, "That employee is already linked to another member.", {
        title: "Conflict",
        status: 409,
        detail: "That employee is already linked to another member.",
      }),
    );
    const { user, onLinked } = open(ADMIN);
    await user.click(screen.getByRole("button", { name: "Create my employee record" }));
    expect(await screen.findByText(/already linked to another member/)).toBeTruthy();
    expect(onLinked).not.toHaveBeenCalled();
  });

  it("asks for a fresh sign-in when the session cannot be refreshed", async () => {
    createEmployee.mockResolvedValue({ id: "emp-new" });
    setMemberEmployee.mockResolvedValue({});
    refreshAccessToken.mockResolvedValue({ outcome: "error" });
    const { user, onLinked } = open(ADMIN);
    await user.click(screen.getByRole("button", { name: "Create my employee record" }));
    expect(await screen.findByText(/sign out and back in once/i)).toBeTruthy();
    expect(onLinked).not.toHaveBeenCalled();
  });

  it("is sent to Employees, not offered a create, when they may link but not create", () => {
    open(["core.identity.membership.write:ORG"]);
    expect(screen.queryByRole("button", { name: "Create my employee record" })).toBeNull();
    expect(screen.getByRole("link", { name: "Go to Employees" })).toBeTruthy();
  });
});

describe("everyone else", () => {
  it("is told who to ask, with no control that could only be refused", () => {
    open([]);
    expect(screen.queryByRole("button", { name: "Create my employee record" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Go to Employees" })).toBeNull();
    expect(screen.getByText(/ask an administrator to link your sign-in/i)).toBeTruthy();
  });

  it("is not offered a create with only the narrower, own-scope grants", () => {
    open(["core.identity.membership.write:OWN", "hrms.people.employee.write:OWN"]);
    expect(screen.queryByRole("button", { name: "Create my employee record" })).toBeNull();
  });
});
