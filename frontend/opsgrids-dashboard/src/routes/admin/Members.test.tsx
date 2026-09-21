import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import { MembersPage } from "./Members";

vi.mock("@/api", () => ({
  listMembers: vi.fn(),
  getMember: vi.fn(),
  listOrgUnits: vi.fn(),
  listLocations: vi.fn(),
  listRoles: vi.fn(),
  listInvitations: vi.fn(),
  createInvitation: vi.fn(),
  revokeInvitation: vi.fn(),
  removeMember: vi.fn(),
  setMemberRoles: vi.fn(),
  updateMember: vi.fn(),
}));

import * as api from "@/api";

const createInvitation = vi.mocked(api.createInvitation);

const emptyPage = { items: [], total: 0, page: 1, size: 25, pages: 0 };

function arrange() {
  vi.mocked(api.listMembers).mockResolvedValue(emptyPage);
  vi.mocked(api.listOrgUnits).mockResolvedValue([]);
  vi.mocked(api.listLocations).mockResolvedValue([]);
  vi.mocked(api.listRoles).mockResolvedValue([]);
  vi.mocked(api.listInvitations).mockResolvedValue([]);
}

function renderPage() {
  return render(
    <PermissionProvider grants={["core.identity.membership.write:ORG", "core.identity.role.write:ORG"]}>
      <MembersPage />
    </PermissionProvider>,
  );
}

async function openInvite(user: ReturnType<typeof userEvent.setup>) {
  // The empty state also offers an "Invite" action once the list has loaded with no rows — the
  // header's own button is the first one in document order.
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Invite" }).length).toBeGreaterThan(0));
  await user.click(screen.getAllByRole("button", { name: "Invite" })[0] as HTMLElement);
  await screen.findByRole("dialog", { name: "Invite somebody" });
}

describe("MembersPage — the invite form and its one-time link", () => {
  it("refuses an invalid email before calling the server", async () => {
    arrange();
    const user = userEvent.setup();
    renderPage();
    await openInvite(user);

    await user.type(screen.getByLabelText("Email"), "not-an-email");
    await user.click(screen.getByRole("button", { name: "Create invitation" }));

    expect(await screen.findByText("Enter a valid email address.")).toBeTruthy();
    expect(createInvitation).not.toHaveBeenCalled();
  });

  it("shows the token exactly once, with a warning that it will not be shown again", async () => {
    arrange();
    createInvitation.mockResolvedValue({
      id: "inv-1",
      email: "asha@greenleaf.example",
      role_codes: [],
      product_codes: [],
      expires_at: "2026-10-04T00:00:00Z",
      accepted_at: null,
      revoked_at: null,
      created_at: "2026-09-20T00:00:00Z",
      token: "one-time-token-value",
    });
    const user = userEvent.setup();
    renderPage();
    await openInvite(user);

    await user.type(screen.getByLabelText("Email"), "asha@greenleaf.example");
    await user.click(screen.getByRole("button", { name: "Create invitation" }));

    expect(await screen.findByText("This link is shown only once.")).toBeTruthy();
    expect(screen.getByText(/accept-invitation\?token=one-time-token-value/)).toBeTruthy();
    expect(createInvitation).toHaveBeenCalledWith({
      email: "asha@greenleaf.example",
      role_codes: [],
      product_codes: [],
      expires_in_days: 14,
    });
  });

  it("maps a 403 privilege-escalation refusal to the form's general error, naming the permission", async () => {
    arrange();
    createInvitation.mockRejectedValue(
      new ApiError(403, "You cannot grant core.identity.role.write, which you do not hold.", {
        status: 403,
        detail: "You cannot grant core.identity.role.write, which you do not hold.",
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await openInvite(user);

    await user.type(screen.getByLabelText("Email"), "asha@greenleaf.example");
    await user.click(screen.getByRole("button", { name: "Create invitation" }));

    expect(await screen.findByText("You cannot grant core.identity.role.write, which you do not hold.")).toBeTruthy();
  });
});
