/**
 * Revealing an employee's sealed details goes through a one-time code: Reveal opens the step-up
 * dialog, and the token the code earns — not the session token — is the bearer of the unmask call
 * for exactly the field asked for. The value shows in place and hides again on Hide. The real
 * step-up, dialog and api client run; the gateway is a stub.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PermissionProvider, clearStepUpToken, setAccessToken } from "@opsgrids/client";

import type { EmployeeOut } from "@/api/types";
import { EmployeeDetailPage } from "./EmployeeDetail";

const EMPLOYEE: EmployeeOut = {
  id: "emp-ananya",
  membership_id: "mem-1",
  employee_no: "ATM-0101",
  full_name: "Ananya Iyer",
  designation: "Engineer",
  job_title: "Engineering",
  department_id: null,
  location_code: "Bengaluru",
  employment_type: "full_time",
  work_mode: "office",
  date_of_joining: "2022-01-01",
  reporting_manager_id: null,
  dotted_line_manager_id: null,
  hierarchy_version: 1,
  status: "active",
  date_of_exit: null,
  created_at: "2022-01-01T00:00:00Z",
  pii: {
    date_of_birth: "••/••/1994",
    personal_email: "a•••@mail.example",
    personal_phone: "•••••• 4821",
    pan: "••••••234Q",
    aadhaar: null,
    uan: null,
    esi: null,
    bank_account: null,
    bank_ifsc: "HDFC0001234",
  },
};

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getEmployee: () => Promise.resolve(EMPLOYEE),
  listEmployeeReports: () => Promise.resolve([]),
}));

vi.mock("@/api/orgUnits", () => ({
  getOrgUnitNameById: () => Promise.resolve(new Map()),
}));

vi.mock("@/api/approvals", () => ({
  getChain: () => new Promise(() => undefined),
}));

const SESSION_TOKEN = "session-access-token";
const STEP_UP_TOKEN = "step-up-access-token";
const UNMASK = "POST /api/v1/employees/emp-ananya/pii/unmask";
const STEP_UP = "POST /api/v1/auth/mfa/step-up";

interface Call {
  key: string;
  authorization: string | undefined;
  body: unknown;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

let calls: Call[];

beforeEach(() => {
  calls = [];
  setAccessToken(SESSION_TOKEN);
  clearStepUpToken();
  const routes: Record<string, () => Response> = {
    [STEP_UP]: () =>
      json({
        status: "complete",
        access_token: STEP_UP_TOKEN,
        token_type: "Bearer",
        expires_in: 180,
        organization_id: "org-1",
        membership_id: "mem-1",
      }),
    [UNMASK]: () => json({ pan: "AAPPK1234Q" }),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const key = `${init?.method ?? "GET"} ${new URL(String(input), "http://localhost").pathname}`;
      calls.push({
        key,
        authorization: headers.Authorization,
        body: typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
      });
      const route = routes[key];
      if (route === undefined) throw new Error(`No stub for ${key}`);
      return Promise.resolve(route());
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
  clearStepUpToken();
});

function renderPage(grants: string[] = ["hrms.people.employee.read_sensitive:OWN"]) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={["/employees/emp-ananya"]}>
        <Routes>
          <Route path="/employees/:employeeId" element={<EmployeeDetailPage />} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

describe("EmployeeDetail reveal", () => {
  it("asks for a one-time code and unmasks one field with the step-up token as the bearer", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Reveal PAN" }));
    expect(screen.getByRole("dialog", { name: "Confirm it's you" })).toBeTruthy();
    await user.type(screen.getByRole("textbox", { name: /6-digit code/ }), "123456");

    expect(await screen.findByText("AAPPK1234Q")).toBeTruthy();
    const unmask = calls.filter((call) => call.key === UNMASK);
    expect(unmask).toHaveLength(1);
    expect(unmask[0]?.authorization).toBe(`Bearer ${STEP_UP_TOKEN}`);
    expect(unmask[0]?.body).toEqual({ fields: ["pan"] });
    expect(screen.queryByText(/A second factor is required/)).toBeNull();

    await user.click(screen.getByRole("button", { name: "Hide PAN" }));
    await waitFor(() => expect(screen.queryByText("AAPPK1234Q")).toBeNull());
    expect(screen.getByText("••••••234Q")).toBeTruthy();
  });

  it("offers no Reveal without the permission, and makes no call", async () => {
    renderPage([]);
    expect(await screen.findByText("••••••234Q")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Reveal/ })).toBeNull();
    expect(calls).toHaveLength(0);
  });
});
