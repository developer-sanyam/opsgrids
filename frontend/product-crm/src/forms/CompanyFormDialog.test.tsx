/**
 * The company form nests another `FormDialog` inside its own — the owner picker's "add a
 * different team member" opens `OwnerFormDialog` from inside `CompanyFormDialog`'s `<form>`.
 * Submitting the inner dialog must never bubble into the outer one: the outer form would then
 * run its own validation against fields the user never touched, show "Give the company a name."
 * and "Choose an owner." for no reason, and steal focus from the dialog the user is actually in.
 */

import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Company, Owner } from "@/api/types";
import { CompanyFormDialog } from "./CompanyFormDialog";

const OWNER: Owner = { id: "own-1", full_name: "Sofia Marchetti", email: "sofia@example.com", role: "AE", is_active: true };
const NEW_OWNER: Owner = { id: "own-2", full_name: "Grace Adeyemi", email: "grace@example.com", role: "AE", is_active: true };

const COMPANY: Company = {
  id: "comp-1",
  name: "Northwind Logistics",
  domain: "northwind.co",
  industry: "Logistics",
  size_band: "501-1000",
  city: "Mumbai",
  country: "India",
  status: "customer",
  owner_id: OWNER.id,
  annual_revenue_minor: 0,
  currency: "INR",
  created_at: "2024-02-11T09:00:00Z",
};

const USER: MeOut = {
  user_id: "user-1",
  email: "priya.nair@opsgrids.example.com",
  full_name: "Priya Nair",
  organization_id: "org-1",
  membership_id: "mem-1",
  roles: ["org_owner"],
  role_names: ["Owner"],
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [],
};

const AUTH_VALUE: AuthContextValue = {
  status: "authenticated",
  user: USER,
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

afterEach(() => {
  vi.restoreAllMocks();
});

/**
 * Mirrors how a real screen wires this up: owners are lifted above the dialog, and
 * `onOwnerCreated` appends to that list — the same list the `Select` reads its options from — so
 * the dialog that opened `OwnerFormDialog` can select what it just created.
 */
function Harness({
  onOwnerCreatedSpy = vi.fn(),
  onSaved = vi.fn(),
}: {
  onOwnerCreatedSpy?: (owner: Owner) => void;
  onSaved?: (company: Company) => void;
}) {
  const [owners, setOwners] = useState<Owner[]>([OWNER]);
  return (
    <AuthContext value={AUTH_VALUE}>
      <PermissionProvider grants={["crm.book.owner.write:ORG"]}>
        <CompanyFormDialog
          company={COMPANY}
          owners={owners}
          onOwnerCreated={(owner) => {
            setOwners((previous) => [...previous, owner]);
            onOwnerCreatedSpy(owner);
          }}
          onClose={vi.fn()}
          onSaved={onSaved}
        />
      </PermissionProvider>
    </AuthContext>
  );
}

function open(props: Parameters<typeof Harness>[0] = {}) {
  render(<Harness {...props} />);
  return userEvent.setup();
}

describe("CompanyFormDialog — nested owner create", () => {
  it("shows no validation errors on the outer form after adding an owner from inside it", async () => {
    vi.spyOn(client, "createOwner").mockResolvedValue(NEW_OWNER);
    const onOwnerCreatedSpy = vi.fn();
    const user = open({ onOwnerCreatedSpy });

    await user.click(screen.getByRole("button", { name: "+ Add a new team member" }));
    screen.getByRole("dialog", { name: "Add a team member" });
    await user.type(screen.getByLabelText(/full name/i), NEW_OWNER.full_name);
    await user.type(screen.getByLabelText(/^email$/i), NEW_OWNER.email);
    await user.click(screen.getByRole("button", { name: /add team member/i }));

    await waitFor(() => expect(onOwnerCreatedSpy).toHaveBeenCalledWith(NEW_OWNER));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Add a team member" })).toBeNull(),
    );

    // The outer form never ran its own submit validation.
    expect(screen.queryByText("Give the company a name.")).toBeNull();
    expect(screen.queryByText("Choose an owner.")).toBeNull();
    // The outer dialog is still open on the company being edited.
    expect(screen.getByRole("dialog", { name: `Edit ${COMPANY.name}` })).toBeTruthy();
  });

  it("selects the newly created owner in the outer form", async () => {
    vi.spyOn(client, "createOwner").mockResolvedValue(NEW_OWNER);
    const user = open();

    await user.click(screen.getByRole("button", { name: "+ Add a new team member" }));
    await user.type(screen.getByLabelText(/full name/i), NEW_OWNER.full_name);
    await user.type(screen.getByLabelText(/^email$/i), NEW_OWNER.email);
    await user.click(screen.getByRole("button", { name: /add team member/i }));

    await waitFor(() =>
      expect((screen.getByLabelText(/^owner$/i) as HTMLSelectElement).value).toBe(NEW_OWNER.id),
    );
  });
});
