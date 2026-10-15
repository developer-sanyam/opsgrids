/**
 * Revealing an employee's sealed details is one tap (ADR-0023 addendum, 2026-10-15): Reveal calls
 * the unmask endpoint for exactly the field asked for, with the ordinary session token, no code
 * dialog. The value shows in place and stays for the session; a second look makes no second call.
 * The real api client runs; the gateway is a stub.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PermissionProvider, clearRevealedValues, setAccessToken } from "@opsgrids/client";

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
const UNMASK = "POST /api/v1/employees/emp-ananya/pii/unmask";

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
  clearRevealedValues();
  const routes: Record<string, () => Response> = {
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
  clearRevealedValues();
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
  it("unmasks one field on one tap, with no code dialog and the session token as the bearer", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(calls).toHaveLength(0);

    await user.click(await screen.findByRole("button", { name: "Reveal PAN" }));

    expect(await screen.findByText("AAPPK1234Q")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    const unmask = calls.filter((call) => call.key === UNMASK);
    expect(unmask).toHaveLength(1);
    expect(unmask[0]?.authorization).toBe(`Bearer ${SESSION_TOKEN}`);
    expect(unmask[0]?.body).toEqual({ fields: ["pan"] });
    expect(calls.some((call) => call.key.includes("/auth/"))).toBe(false);
    // It stays: no Hide control.
    expect(screen.queryByRole("button", { name: "Hide PAN" })).toBeNull();
  });

  it("shows the value again, with no second call, when the page is opened again in the same session", async () => {
    const user = userEvent.setup();
    const first = renderPage();
    await user.click(await screen.findByRole("button", { name: "Reveal PAN" }));
    await screen.findByText("AAPPK1234Q");
    first.unmount();

    renderPage();

    expect(await screen.findByText("AAPPK1234Q")).toBeTruthy();
    expect(calls.filter((call) => call.key === UNMASK)).toHaveLength(1);
  });

  it("offers no Reveal without the permission, and makes no call", async () => {
    renderPage([]);
    expect(await screen.findByText("••••••234Q")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Reveal/ })).toBeNull();
    expect(calls).toHaveLength(0);
  });
});
