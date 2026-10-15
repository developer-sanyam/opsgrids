/**
 * "Linked member" on the employee page (ADR-0008 Q1): which sign-in is this employee record.
 * Renders only for someone who may write memberships at ORG; the server's own floor is the control.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import type { MemberOut } from "@/api/members";
import type { EmployeeOut } from "@/api/types";

const getMemberLinkedTo = vi.fn();
const listUnlinkedMembers = vi.fn();
const setMemberEmployee = vi.fn();

vi.mock("@/api/members", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/members")>()),
  getMemberLinkedTo: (...a: unknown[]) => getMemberLinkedTo(...a),
  listUnlinkedMembers: (...a: unknown[]) => listUnlinkedMembers(...a),
  setMemberEmployee: (...a: unknown[]) => setMemberEmployee(...a),
}));

import { LinkedMemberCard } from "./LinkedMemberCard";

const EMPLOYEE = { id: "emp-1", full_name: "Ananya Iyer" } as EmployeeOut;

const MEMBER: MemberOut = {
  id: "mem-9",
  user_id: "user-9",
  email: "ananya@acme.example",
  full_name: "Ananya Iyer",
  display_name: null,
  status: "active",
  is_owner: false,
  employee_id: "emp-1",
};
const OTHER: MemberOut = { ...MEMBER, id: "mem-10", email: "ravi@acme.example", full_name: "Ravi Kapoor", employee_id: null };

function open(grants: string[]) {
  render(
    <PermissionProvider grants={grants}>

      <LinkedMemberCard employee={EMPLOYEE} />

    </PermissionProvider>,
  );
  return userEvent.setup();
}

const ADMIN = ["core.identity.membership.write:ORG"];

beforeEach(() => {
  getMemberLinkedTo.mockReset();
  listUnlinkedMembers.mockReset();
  setMemberEmployee.mockReset();
});

afterEach(() => vi.restoreAllMocks());

describe("who sees it", () => {
  it("renders nothing, and asks nothing, for a role that may not write memberships", () => {
    open(["core.identity.membership.read:ORG", "core.identity.membership.write:OWN"]);
    expect(screen.queryByText("Linked member")).toBeNull();
    expect(getMemberLinkedTo).not.toHaveBeenCalled();
  });
});

describe("an employee with no linked sign-in", () => {
  beforeEach(() => {
    getMemberLinkedTo.mockResolvedValue(null);
    listUnlinkedMembers.mockResolvedValue([OTHER]);
  });

  it("offers the logins that have no employee, and links the one chosen", async () => {
    setMemberEmployee.mockResolvedValue({ ...OTHER, employee_id: "emp-1" });
    const user = open(ADMIN);

    const select = await screen.findByLabelText("Sign-in");
    expect(screen.getByRole("button", { name: "Link" })).toHaveProperty("disabled", true);
    await user.selectOptions(select, "mem-10");
    await user.click(screen.getByRole("button", { name: "Link" }));

    await waitFor(() => expect(setMemberEmployee).toHaveBeenCalledWith("mem-10", "emp-1"));
  });

  it("says in the server's words that the employee is already linked elsewhere", async () => {
    setMemberEmployee.mockRejectedValue(
      new ApiError(409, "That employee is already linked to another member.", {
        title: "Conflict",
        status: 409,
        detail: "That employee is already linked to another member.",
      }),
    );
    const user = open(ADMIN);
    await user.selectOptions(await screen.findByLabelText("Sign-in"), "mem-10");
    await user.click(screen.getByRole("button", { name: "Link" }));
    expect(await screen.findByText(/already linked to another member/)).toBeTruthy();
  });

  it("explains an empty picker rather than showing an empty dropdown", async () => {
    listUnlinkedMembers.mockResolvedValue([]);
    open(ADMIN);
    expect(await screen.findByText(/already linked to an employee/i)).toBeTruthy();
    expect(screen.queryByLabelText("Sign-in")).toBeNull();
  });
});

describe("an employee with a linked sign-in", () => {
  beforeEach(() => getMemberLinkedTo.mockResolvedValue(MEMBER));

  it("names them, and unlinking asks first -- and says it refreshes everyone's sign-in", async () => {
    setMemberEmployee.mockResolvedValue({ ...MEMBER, employee_id: null });
    const user = open(ADMIN);

    expect(await screen.findByText("ananya@acme.example")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Unlink" }));
    const dialog = await screen.findByRole("dialog", { name: /unlink this sign-in/i });
    expect(dialog.textContent).toMatch(/refreshes everyone's sign-in/i);
    expect(setMemberEmployee).not.toHaveBeenCalled();

    await user.click(screen.getAllByRole("button", { name: "Unlink" }).at(-1)!);
    await waitFor(() => expect(setMemberEmployee).toHaveBeenCalledWith("mem-9", null));
  });

  it("does not unlink when the confirmation is cancelled", async () => {
    const user = open(ADMIN);
    await screen.findByText("ananya@acme.example");
    await user.click(screen.getByRole("button", { name: "Unlink" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(setMemberEmployee).not.toHaveBeenCalled();
  });
});
