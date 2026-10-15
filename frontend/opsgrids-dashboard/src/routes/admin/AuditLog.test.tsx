import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue } from "@opsgrids/client";

import type { AuditEntry, AuditPage } from "@/api/types";

import { AuditLogPage } from "./AuditLog";

vi.mock("@/api", () => ({
  listAuditLog: vi.fn(),
  listMembers: vi.fn(),
}));

import * as api from "@/api";

const listAuditLog = vi.mocked(api.listAuditLog);

const reveal: AuditEntry = {
  id: "r1",
  at: "2026-10-01T11:00:00+00:00",
  service: "customer",
  product: "crm",
  actor_id: "user-bob",
  by_operator: false,
  action: "entity.sensitive_read",
  entity: "contact",
  entity_id: "4f1c2a9e-0000-0000-0000-000000000000",
  fields: ["phone"],
  sensitive: true,
  reason: null,
};

const invite: AuditEntry = {
  ...reveal,
  id: "i1",
  at: "2026-10-01T09:00:00+00:00",
  service: "account",
  product: "core",
  actor_id: "user-alice",
  action: "member.invited",
  entity: "membership",
  fields: ["role"],
  sensitive: false,
};

function page(service: string, items: AuditEntry[]): AuditPage {
  return { service, product: "core", items, next_cursor: null };
}

const auth: AuthContextValue = {
  status: "authenticated",
  user: {
    user_id: "user-me",
    email: "me@example.com",
    full_name: "Casey Rivera",
    organization_id: "org-a",
    membership_id: "m-1",
    roles: [],
    role_names: [],
    amr: [],
    must_change_password: false,
    mfa_enabled: false,
    organizations: [],
  },
  login: () => Promise.reject(new Error("not used")),
  verifyMfa: () => Promise.reject(new Error("not used")),
  selectOrg: () => Promise.reject(new Error("not used")),
  logout: () => Promise.resolve(),
};

function renderPage(grants: string[] = ["core.identity.audit.read:ORG", "core.identity.membership.read:ORG"]) {
  return render(
    <AuthContext value={auth}>
      <PermissionProvider grants={grants} entitlements={["crm"]}>
        <AuditLogPage />
      </PermissionProvider>
    </AuthContext>,
  );
}

beforeEach(() => {
  vi.mocked(api.listMembers).mockResolvedValue({
    items: [
      { user_id: "user-alice", full_name: "Alice Rao", display_name: null },
      { user_id: "user-bob", full_name: "Bob Iyer", display_name: null },
    ] as never,
    total: 2,
    page: 1,
    size: 200,
    pages: 1,
  });
  listAuditLog.mockReset();
});

describe("AuditLogPage", () => {
  it("merges every active source newest first and names who revealed which field", async () => {
    listAuditLog.mockImplementation(async (service) => {
      if (service === "customer") return page(service, [reveal]);
      if (service === "account") return page(service, [invite]);
      return page(service, []);
    });
    renderPage();

    expect(await screen.findAllByText("Revealed phone")).not.toHaveLength(0);
    expect(screen.getAllByText("Bob Iyer").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Member invited").length).toBeGreaterThan(0);
    // Only the platform's own services and the CRM services are asked: no other product is active.
    const asked = listAuditLog.mock.calls.map(([service]) => service).sort();
    expect(asked).toEqual([
      "account",
      "approval",
      "auth",
      "billing",
      "customer",
      "deal",
      "document",
      "lead",
      "payment",
      "subscription",
    ]);
  });

  it("filters to personal-data reveals on the server, not by hiding rows", async () => {
    listAuditLog.mockResolvedValue(page("account", []));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("No activity recorded yet");

    await user.selectOptions(screen.getByLabelText("Type"), "reveals");
    await waitFor(() =>
      expect(listAuditLog).toHaveBeenCalledWith("account", expect.objectContaining({ action: "entity.sensitive_read" })),
    );
    expect(await screen.findByText("No activity matches these filters")).toBeTruthy();
  });

  it("sends a date range as instants, with the end day included", async () => {
    listAuditLog.mockResolvedValue(page("account", []));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("No activity recorded yet");

    await user.type(screen.getByLabelText("From"), "2026-10-01");
    await user.type(screen.getByLabelText("To"), "2026-10-03");
    await waitFor(() => {
      const last = listAuditLog.mock.calls.at(-1)?.[1];
      expect(last?.from).toMatch(/^2026-(09-30|10-01)T/);
      expect(last?.to).toMatch(/^2026-(10-03|10-04)T/);
    });
  });

  it("still shows the other sources when one fails, and says which", async () => {
    listAuditLog.mockImplementation(async (service) => {
      if (service === "deal") throw new ApiError(500, "Upstream failed", null);
      if (service === "account") return page(service, [invite]);
      return page(service, []);
    });
    renderPage();
    expect(await screen.findByText(/Some activity couldn't be loaded: CRM: deals/)).toBeTruthy();
    expect(screen.getAllByText("Member invited").length).toBeGreaterThan(0);
  });

  it("explains a refusal instead of showing an empty log", async () => {
    listAuditLog.mockRejectedValue(new ApiError(403, "Missing permission", null));
    renderPage();
    expect(await screen.findByText("You don't have access to the audit log")).toBeTruthy();
  });
});
