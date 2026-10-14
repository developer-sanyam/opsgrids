/**
 * `EmployeesPage` and its Import / Export button: after an import that creates employees the list
 * asks the server again by itself, so the new people show up without a refresh. The hook, the
 * dialog and the screen are all real; only the gateway is stubbed, and it holds a directory that
 * a `POST /employees` adds to.
 */
import { PermissionProvider } from "@opsgrids/client";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExcelJS from "exceljs";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EmployeeOut } from "@/api/types";
import { employeesEntity } from "@/data/importExport/employees";

import { EmployeesPage } from "./Employees";

const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const PAGE_SIZE = 9;

function employee(number: number): EmployeeOut {
  const padded = String(number).padStart(4, "0");
  return {
    id: `e-${padded}`,
    membership_id: null,
    employee_no: `EMP-${padded}`,
    full_name: `Person ${padded}`,
    designation: null,
    job_title: null,
    department_id: null,
    location_code: null,
    employment_type: "full_time",
    work_mode: "office",
    date_of_joining: null,
    reporting_manager_id: null,
    dotted_line_manager_id: null,
    status: "probation",
    date_of_exit: null,
    created_at: "2026-01-05T09:00:00Z",
    hierarchy_version: 1,
    pii: null,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** A directory served with real paging, which `POST /employees` appends to. */
function stubGateway(initial: number) {
  const directory = Array.from({ length: initial }, (_, index) => employee(index + 1));
  const screenLists: URLSearchParams[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, "http://gateway.test");
      const method = init?.method ?? "GET";
      if (method === "GET" && url.pathname === "/api/v1/org-units") return json(200, []);
      if (method === "GET" && url.pathname === "/api/v1/locations") return json(200, []);
      if (method === "GET" && url.pathname === "/api/v1/employees") {
        const page = Number(url.searchParams.get("page") ?? "1");
        const size = Number(url.searchParams.get("size") ?? "25");
        if (size === PAGE_SIZE) screenLists.push(url.searchParams);
        return json(200, {
          items: directory.slice((page - 1) * size, page * size),
          total: directory.length,
          page,
          size,
          pages: Math.max(1, Math.ceil(directory.length / size)),
        });
      }
      if (method === "POST" && url.pathname === "/api/v1/employees" && typeof init?.body === "string") {
        const body: { employee_no: string; full_name: string } = JSON.parse(init.body);
        const created = { ...employee(directory.length + 1), ...body, id: `new-${body.employee_no}` };
        directory.push(created);
        return json(201, created);
      }
      throw new Error(`Unexpected request: ${method} ${input}`);
    }),
  );
  return { screenLists: () => screenLists };
}

async function importFile(rows: readonly Record<string, ExcelJS.CellValue>[]): Promise<File> {
  const columns = employeesEntity.columns.filter((column) => column.readOnly !== true);
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow(columns.map((column) => (column.required === true ? `${column.header} *` : column.header)));
  for (const row of rows) sheet.addRow(columns.map((column) => row[column.key] ?? null));
  return new File([await workbook.xlsx.writeBuffer()], "employees.xlsx", { type: XLSX_TYPE });
}

function renderPage() {
  return render(
    <MemoryRouter>
      <PermissionProvider grants={["hrms.people.employee.write:ORG"]}>
        <EmployeesPage />
      </PermissionProvider>
    </MemoryRouter>,
  );
}

/** Opens the dialog, chooses a file of `rows`, and imports every row that passes. */
async function importThrough(user: ReturnType<typeof userEvent.setup>, rows: readonly Record<string, ExcelJS.CellValue>[]) {
  await user.click(screen.getByRole("button", { name: "Import / Export" }));
  await screen.findByRole("dialog", { name: "Import / Export employees" });
  const input = document.querySelector<HTMLInputElement>("input[type=file]");
  if (input === null) throw new Error("The dialog has no file input");
  await user.upload(input, await importFile(rows));
  await screen.findByRole("dialog", { name: "Review your file" });
  await user.click(screen.getByRole("button", { name: /^Import \d+ rows?$/ }));
  await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 5_000 });
  await user.click(screen.getByRole("button", { name: "Done" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("EmployeesPage: importing from Excel", () => {
  it("shows the employees an import created without a refresh", async () => {
    const gateway = stubGateway(2);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Person 0002");
    expect(screen.queryByText("Nikhil Rao")).toBeNull();
    expect(gateway.screenLists()).toHaveLength(1);

    await importThrough(user, [{ employee_no: "EMP-4001", full_name: "Nikhil Rao" }]);

    expect(await screen.findByText("Nikhil Rao")).toBeTruthy();
    expect(screen.getByText("Person 0001")).toBeTruthy();
    // One more request for the list, from the first page, with the screen's own page size.
    expect(gateway.screenLists()).toHaveLength(2);
    expect(Object.fromEntries(gateway.screenLists()[1] ?? [])).toEqual({ page: "1", size: String(PAGE_SIZE) });
  }, 15_000);

  it("starts the list over from the first page when more pages had been loaded", async () => {
    const gateway = stubGateway(12);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Person 0009");
    await user.click(screen.getByRole("button", { name: /^Load more/ }));
    await screen.findByText("Person 0012");
    expect(screen.getAllByRole("link")).toHaveLength(12);

    await importThrough(user, [{ employee_no: "EMP-4001", full_name: "Nikhil Rao" }]);

    // Thirteen people, and the window is back to the first nine with the rest to load: no person twice.
    await waitFor(() => expect(screen.getByRole("button", { name: /^Load more · 4 left$/ })).toBeTruthy());
    expect(screen.getAllByRole("link")).toHaveLength(9);
    expect(screen.getAllByText("Person 0001")).toHaveLength(1);
    const lastRequest = gateway.screenLists().at(-1);
    expect(lastRequest?.get("page")).toBe("1");
  }, 15_000);
});

describe("EmployeesPage: loading more", () => {
  it("adds each further page once, so no person is listed twice", async () => {
    const gateway = stubGateway(12);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Person 0009");
    expect(screen.getAllByRole("link")).toHaveLength(9);

    await user.click(screen.getByRole("button", { name: /^Load more · 3 left$/ }));
    await screen.findByText("Person 0012");

    expect(screen.getAllByRole("link")).toHaveLength(12);
    expect(screen.getAllByText("Person 0001")).toHaveLength(1);
    expect(screen.getByText("Showing all 12 employees")).toBeTruthy();
    expect(gateway.screenLists().map((params) => params.get("page"))).toEqual(["1", "2"]);
  });
});

describe("EmployeesPage: Import / Export on a phone", () => {
  function stubPhone(): void {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }));
  }

  it("moves into More actions, and opens the same dialog from there", async () => {
    const gateway = stubGateway(2);
    stubPhone();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Person 0002");
    expect(gateway.screenLists()).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Import / Export" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Import / Export" }));

    expect(await screen.findByRole("dialog", { name: "Import / Export employees" })).toBeTruthy();
  });
});
