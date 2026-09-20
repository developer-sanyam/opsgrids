/**
 * §3.3, §4.E — the revoke confirmation: the happy path, the already-revoked 409, and keyboard
 * operability (a real modal, closable with Escape).
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";

import type { DelegationOut } from "@/api/types";
import { RevokeDelegationDialog } from "./RevokeDelegationDialog";

const revokeDelegation = vi.fn();

vi.mock("@/api/approvals", () => ({
  revokeDelegation: (...args: unknown[]) => revokeDelegation(...args),
}));

const DELEGATION: DelegationOut = {
  id: "del-1",
  delegator_employee_id: "emp-sneha",
  delegator_name: "Sneha Kulkarni",
  delegator_location_code: "Bengaluru",
  delegate_employee_id: "emp-rohan",
  delegate_name: "Rohan Mehta",
  starts_at: "2026-09-15T03:30:00Z",
  ends_at: "2026-09-22T03:30:00Z",
  status: "active",
  created_by_employee_id: "emp-sneha",
  revoked_at: null,
  revoked_by_employee_id: null,
};

function renderDialog() {
  const onClose = vi.fn();
  const onRevoked = vi.fn();
  const onAlreadyRevoked = vi.fn();
  render(
    <RevokeDelegationDialog
      open
      onClose={onClose}
      delegation={DELEGATION}
      myEmployeeId="emp-sneha"
      onRevoked={onRevoked}
      onAlreadyRevoked={onAlreadyRevoked}
    />,
  );
  return { onClose, onRevoked, onAlreadyRevoked };
}

describe("RevokeDelegationDialog", () => {
  beforeEach(() => {
    revokeDelegation.mockReset();
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
  });

  it("names the delegate and states revocation is immediate and final", () => {
    renderDialog();
    expect(
      screen.getByText(
        "Rohan Mehta will stop being able to decide for you immediately. Anything already with them stays with them and is still yours to decide. This can't be undone.",
      ),
    ).toBeDefined();
  });

  it("revokes and reports the revoked row back", async () => {
    const revoked = { ...DELEGATION, status: "revoked" as const, revoked_at: "2026-09-16T10:00:00Z", revoked_by_employee_id: "emp-sneha" };
    revokeDelegation.mockResolvedValue(revoked);
    const user = userEvent.setup();
    const { onRevoked } = renderDialog();

    await user.click(screen.getByRole("button", { name: "Revoke" }));

    await waitFor(() => expect(onRevoked).toHaveBeenCalledWith(revoked));
    expect(revokeDelegation).toHaveBeenCalledWith("del-1");
  });

  it("hands back a clean signal on a 409 — already revoked", async () => {
    revokeDelegation.mockRejectedValue(new ApiError(409, "This delegation was already revoked", null));
    const user = userEvent.setup();
    const { onAlreadyRevoked } = renderDialog();

    await user.click(screen.getByRole("button", { name: "Revoke" }));

    await waitFor(() => expect(onAlreadyRevoked).toHaveBeenCalled());
  });

  it("blocks confirm while offline", async () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: "Revoke" }));

    expect(screen.getByText("You're offline. Reconnect to send this.")).toBeDefined();
    expect(revokeDelegation).not.toHaveBeenCalled();
  });

  it("is a real modal, closable with Escape, and 'Keep it' cancels without revoking", async () => {
    const user = userEvent.setup();
    const { onClose } = renderDialog();

    expect(screen.getByRole("dialog", { name: "Revoke this delegation?" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Keep it" })).toBeDefined();

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
    expect(revokeDelegation).not.toHaveBeenCalled();
  });
});
