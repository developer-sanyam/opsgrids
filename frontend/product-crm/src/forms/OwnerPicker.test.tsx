/**
 * The empty directory is the one state every deal / company / contact create hits in a brand new
 * organization, and it used to be a dead end for the org owner themselves: "add your first team
 * member" with no way to do that except retyping the name and email already sitting in the
 * session. These tests pin the one-click path that replaced it, and that the permission-denied
 * and non-empty states around it are unchanged.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Owner } from "@/api/types";
import { OwnerPicker } from "./OwnerPicker";

const USER: MeOut = {
  user_id: "user-1",
  email: "priya.nair@opsgrids.example.com",
  full_name: "Priya Nair",
  organization_id: "org-1",
  membership_id: "mem-1",
  roles: ["org_owner"],
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [],
};

const OWNER: Owner = { id: "own-1", full_name: "Grace Adeyemi", email: "grace@example.com", role: "AE", is_active: true };

const AUTH_VALUE: AuthContextValue = {
  status: "authenticated",
  user: USER,
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

function Harness({
  owners = [],
  signedIn = true,
  grants = ["crm.book.owner.write:ORG"],
  onOwnerCreated = vi.fn(),
  me = USER,
}: {
  owners?: Owner[];
  signedIn?: boolean;
  grants?: string[];
  onOwnerCreated?: (owner: Owner) => void;
  me?: MeOut;
}) {
  const body = (
    <PermissionProvider grants={grants}>
      <OwnerPicker owners={owners} value="" onChange={vi.fn()} onOwnerCreated={onOwnerCreated} />
    </PermissionProvider>
  );
  return signedIn ? <AuthContext value={{ ...AUTH_VALUE, user: me }}>{body}</AuthContext> : body;
}

function open(props: Parameters<typeof Harness>[0] = {}) {
  render(<Harness {...props} />);
  return userEvent.setup();
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OwnerPicker — empty directory", () => {
  it("offers a one-click claim naming the signed-in user, holding org-level write", () => {
    open();
    expect(screen.getByRole("button", { name: "Make Priya Nair the owner" })).toBeTruthy();
  });

  it("creates the owner from the session's name and email, not a typed form", async () => {
    vi.spyOn(client, "createOwner").mockResolvedValue({
      id: "own-new",
      full_name: USER.full_name,
      email: USER.email,
      role: "",
      is_active: true,
    });
    const onOwnerCreated = vi.fn();
    const user = open({ onOwnerCreated });

    await user.click(screen.getByRole("button", { name: "Make Priya Nair the owner" }));

    await waitFor(() =>
      expect(client.createOwner).toHaveBeenCalledWith({
        full_name: "Priya Nair",
        email: "priya.nair@opsgrids.example.com",
      }),
    );
    expect(onOwnerCreated).toHaveBeenCalledWith(
      expect.objectContaining({ id: "own-new", full_name: "Priya Nair" }),
    );
  });

  it("disables the claim button while the write is in flight, so a second click cannot double-submit", async () => {
    let resolve!: (value: Owner) => void;
    vi.spyOn(client, "createOwner").mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const user = open();

    const claim = screen.getByRole("button", { name: "Make Priya Nair the owner" });
    await user.click(claim);
    expect(claim).toHaveProperty("disabled", true);
    await user.click(claim); // ignored — one write in flight

    resolve(OWNER);
    await waitFor(() => expect(client.createOwner).toHaveBeenCalledTimes(1));
  });

  it("surfaces a failed claim inline without losing the option to try again", async () => {
    vi.spyOn(client, "createOwner").mockRejectedValue(
      new ApiError(409, "A team member with that email already exists.", {
        title: "Conflict",
        status: 409,
        detail: "A team member with that email already exists.",
      }),
    );
    const user = open();

    await user.click(screen.getByRole("button", { name: "Make Priya Nair the owner" }));

    expect(await screen.findByText(/a team member with that email already exists/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Make Priya Nair the owner" })).toHaveProperty(
      "disabled",
      false,
    );
  });

  it("keeps a secondary path to add someone other than the signed-in user", async () => {
    const user = open();
    await user.click(screen.getByRole("button", { name: "Add a different team member" }));
    expect(screen.getByRole("dialog", { name: "Add a team member" })).toBeTruthy();
  });

  it("offers no claim at all without org-level owner-write, and no button that could only be refused", () => {
    open({ grants: [] });
    expect(screen.queryByRole("button", { name: /make .* the owner/i })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add a different team member" })).toBeNull();
    expect(screen.getByText(/ask an administrator to add the team/i)).toBeTruthy();
  });

  it("offers the full form instead of a claim when the session has no name to claim with", () => {
    open({ me: { ...USER, full_name: "  " } });
    expect(screen.queryByRole("button", { name: /make .* the owner/i })).toBeNull();
    expect(screen.getByRole("button", { name: "Add a different team member" })).toBeTruthy();
  });

  it("offers no one-click claim when rendered with no signed-in session", () => {
    open({ signedIn: false });
    expect(screen.queryByRole("button", { name: /make .* the owner/i })).toBeNull();
    // The org-level grant alone still offers the full form.
    expect(screen.getByRole("button", { name: "Add a different team member" })).toBeTruthy();
  });
});

describe("OwnerPicker — populated directory", () => {
  it("renders the Select instead of the claim, and defaults to nothing selected", () => {
    open({ owners: [OWNER] });
    expect(screen.queryByRole("button", { name: /make .* the owner/i })).toBeNull();
    expect((screen.getByLabelText(/^owner$/i) as HTMLSelectElement).value).toBe("");
  });
});
