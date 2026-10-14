import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PermissionProvider } from "@opsgrids/client";

import { RolesPage } from "./Roles";

vi.mock("@/api", () => ({
  listRoles: vi.fn(),
  listPermissions: vi.fn(),
  createRole: vi.fn(),
  updateRole: vi.fn(),
  deleteRole: vi.fn(),
}));

import * as api from "@/api";

const permission = (resource: string, action: string, description: string) => ({
  code: `core.identity.${resource}.${action}`,
  product: "core",
  module: "identity",
  resource,
  action,
  description,
  default_scope: "ORG" as const,
  max_scope: "ORG" as const,
  is_dangerous: false,
});

describe("RolesPage - the role form's permission filter", () => {
  it("narrows the permission list without touching what is granted, and says when nothing matches", async () => {
    vi.mocked(api.listRoles).mockResolvedValue([]);
    vi.mocked(api.listPermissions).mockResolvedValue([
      permission("member", "read", "See the people in the organisation"),
      permission("invoice", "read", "See invoices"),
    ] as never);
    const user = userEvent.setup();
    render(
      <PermissionProvider grants={["core.identity.role.write:ORG"]}>
        <RolesPage />
      </PermissionProvider>,
    );

    await waitFor(() => expect(screen.getAllByRole("button", { name: /New role|Create role/ }).length).toBeGreaterThan(0));
    await user.click(screen.getAllByRole("button", { name: /New role|Create role/ })[0] as HTMLElement);
    const dialog = await screen.findByRole("dialog", { name: "New role" });

    expect(within(dialog).getByText("member.read")).toBeTruthy();
    expect(within(dialog).getByText("invoice.read")).toBeTruthy();

    // Tick one, then filter it away: it stays granted.
    await user.click(within(dialog).getAllByRole("checkbox")[0] as HTMLElement);
    await user.type(within(dialog).getByLabelText("Filter permissions"), "invoice");
    expect(within(dialog).queryByText("member.read")).toBeNull();
    expect(within(dialog).getByText("invoice.read")).toBeTruthy();

    await user.clear(within(dialog).getByLabelText("Filter permissions"));
    expect((within(dialog).getAllByRole("checkbox")[0] as HTMLInputElement).checked).toBe(true);

    await user.type(within(dialog).getByLabelText("Filter permissions"), "zzz");
    expect(within(dialog).getByText("No permissions match that filter.")).toBeTruthy();
  });
});
