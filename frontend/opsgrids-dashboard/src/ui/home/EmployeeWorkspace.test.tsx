import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiError, AuthContext, PermissionProvider, setAccessToken } from "@opsgrids/client";
import type { AuthContextValue } from "@opsgrids/client";

import { EmployeeWorkspace } from "./EmployeeWorkspace";

vi.mock("@/api", () => ({
  getMyAttendance: vi.fn(),
  listMyLeaveBalances: vi.fn(),
  listMyTasks: vi.fn(),
}));

import * as api from "@/api";

const AUTH: AuthContextValue = {
  status: "authenticated",
  user: {
    user_id: "user-1",
    email: "anjali@acme.example",
    full_name: "Anjali Rao",
    organization_id: "org-1",
    membership_id: "member-1",
    roles: ["MEMBER"],
    amr: [],
    must_change_password: false,
    mfa_enabled: false,
    organizations: [{ organization_id: "org-1", slug: "acme", name: "Acme Industries", roles: ["MEMBER"] }],
  },
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

const EMPLOYEE_GRANTS = [
  "projects.board.task.read:OWN",
  "hrms.time.attendance.read:OWN",
  "hrms.time.attendance.write:OWN",
  "hrms.time.leave_balance.read:OWN",
  "hrms.time.leave_request.write:OWN",
  "hrms.people.employee.read:OWN",
];

function page<T>(items: T[]) {
  return { items, total: items.length, page: 1, size: Math.max(items.length, 1), pages: 1 };
}

// A fixed `sub`/`org`/`sref` header+payload+signature, unverified here (as `decodeAccessToken`
// itself documents) — enough for `EmployeeWorkspace` to read a `sref` claim from.
const FIXTURE_TOKEN =
  "eyJhbGciOiJub25lIn0." +
  btoa(JSON.stringify({ sref: "emp-1", ent: ["hrms", "projects"], act_org: "org-1" })) +
  ".sig";

function renderWorkspace(options: { grants?: string[]; entitlements?: string[] } = {}) {
  setAccessToken(FIXTURE_TOKEN);
  const grants = options.grants ?? EMPLOYEE_GRANTS;
  const entitlements = options.entitlements ?? ["hrms", "projects"];
  return render(
    <MemoryRouter>
      <AuthContext value={AUTH}>
        <PermissionProvider grants={grants} entitlements={entitlements}>
          <EmployeeWorkspace />
        </PermissionProvider>
      </AuthContext>
    </MemoryRouter>,
  );
}

const TASK = {
  id: "task-1",
  title: "Update product images",
  stage: "in_progress" as const,
  priority: "medium" as const,
  due_date: "2026-09-24",
  project_code: "STORE",
};

describe("EmployeeWorkspace — populated", () => {
  it("shows the greeting, real tasks, attendance and leave balance", async () => {
    vi.mocked(api.listMyTasks).mockResolvedValue(page([TASK]));
    vi.mocked(api.getMyAttendance).mockResolvedValue(
      page([{ id: "att-1", work_date: "2026-09-25", check_in_at: "2026-09-25T03:45:00Z", check_out_at: null, status: "present" }]),
    );
    vi.mocked(api.listMyLeaveBalances).mockResolvedValue([
      { employee_id: "emp-1", leave_type_id: "lt-1", leave_type_code: "EL", balance_half_days: 24 },
    ]);

    renderWorkspace();

    expect(await screen.findByRole("heading", { name: /Anjali/ })).toBeTruthy();
    expect(screen.getByText("Here's your work for today.")).toBeTruthy();
    expect(await screen.findByText("Update product images")).toBeTruthy();
    expect(await screen.findByText(/Checked in at/)).toBeTruthy();
    expect(await screen.findByText("12")).toBeTruthy();
    expect(screen.getByText("EL")).toBeTruthy();

    // Quick actions this grant set covers.
    expect(screen.getByRole("link", { name: /Apply Leave/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Mark Attendance/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /My Profile/ })).toBeTruthy();
  });
});

describe("EmployeeWorkspace — empty", () => {
  it("shows the approved all-set copy, not checked in, and the no-leave-types notice", async () => {
    vi.mocked(api.listMyTasks).mockResolvedValue(page([]));
    vi.mocked(api.getMyAttendance).mockResolvedValue(page([]));
    vi.mocked(api.listMyLeaveBalances).mockResolvedValue([]);

    renderWorkspace();

    expect(await screen.findByText("You're all set!")).toBeTruthy();
    expect(await screen.findByText("No pending tasks for today.")).toBeTruthy();
    expect(await screen.findByText("Not checked in yet")).toBeTruthy();
    expect(await screen.findByText("No leave types defined yet")).toBeTruthy();
  });
});

describe("EmployeeWorkspace — error", () => {
  it("shows the server's own detail and a retry affordance for a failed tasks fetch", async () => {
    vi.mocked(api.listMyTasks).mockRejectedValue(
      new ApiError(503, "Service Unavailable", { title: "Service Unavailable", detail: "network down" }),
    );
    vi.mocked(api.getMyAttendance).mockResolvedValue(page([]));
    vi.mocked(api.listMyLeaveBalances).mockResolvedValue([]);

    renderWorkspace();

    expect(await screen.findByText("network down")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});

describe("EmployeeWorkspace — plan-gated and no-access", () => {
  it("shows the not-active state and never fetches Projects data when it isn't entitled", async () => {
    renderWorkspace({ grants: [], entitlements: [] });

    expect(await screen.findByText("Projects isn't active for your organisation.")).toBeTruthy();
    expect(api.listMyTasks).not.toHaveBeenCalled();
    expect(api.getMyAttendance).not.toHaveBeenCalled();
    expect(api.listMyLeaveBalances).not.toHaveBeenCalled();
  });

  it("shows No access and never fetches what an entitled caller holds no grant for", async () => {
    renderWorkspace({ grants: [], entitlements: ["projects", "hrms"] });

    const notices = await screen.findAllByText("No access");
    expect(notices.length).toBeGreaterThan(0);
    expect(api.listMyTasks).not.toHaveBeenCalled();
    expect(api.getMyAttendance).not.toHaveBeenCalled();
    expect(api.listMyLeaveBalances).not.toHaveBeenCalled();
  });

  it("renders no HRMS card at all once HRMS is not entitled — not a plan-gated notice", async () => {
    vi.mocked(api.listMyTasks).mockResolvedValue(page([]));
    renderWorkspace({ grants: EMPLOYEE_GRANTS, entitlements: ["projects"] });

    await waitFor(() => expect(screen.getByText("You're all set!")).toBeTruthy());
    expect(screen.queryByText("Attendance Today")).toBeNull();
    expect(screen.queryByText("Leave Balance")).toBeNull();
    expect(screen.queryByRole("link", { name: /Apply Leave/ })).toBeNull();
  });
});
