/**
 * `PortfolioPage` and its Import / Export button: after an import that creates projects the list
 * asks the server again by itself, so the new projects show up without a refresh. The hook, the
 * dialog and the screen are all real; only the gateway is stubbed, and it holds a portfolio that
 * a `POST /projects-workspace/projects` adds to.
 */
import { PermissionProvider } from "@opsgrids/client";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExcelJS from "exceljs";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MemberOut, ProjectIn, ProjectOut } from "@/api/types";
import { portfolioEntity } from "@/data/importExport/portfolio";

import { PortfolioPage } from "./Portfolio";

const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

const MEMBERS: MemberOut[] = [
  { id: "p-01", name: "Meera Iyer", role: "Delivery lead", directory_id: null },
  { id: "p-02", name: "Arjun Rao", role: "Engineer", directory_id: null },
];

function project(over: Partial<ProjectOut>): ProjectOut {
  return {
    id: "proj-1",
    code: "PRJ-104",
    name: "Customer portal redesign",
    client: "Northwind Traders",
    owner: { id: "p-01", name: "Meera Iyer", role: "Delivery lead" },
    phase: "build",
    health: "on_track",
    progress_pct: 45,
    start_date: "2026-03-02",
    target_date: "2026-09-30",
    budget_minor: 250000000,
    spent_minor: 112000000,
    currency: "INR",
    team_size: 6,
    open_tasks: 12,
    milestone_count: 4,
    summary: "",
    ...over,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** A portfolio served with real paging, which `POST /projects-workspace/projects` appends to. */
function stubGateway() {
  const portfolio = [
    project({ id: "proj-1", code: "PRJ-104", name: "Customer portal redesign" }),
    project({ id: "proj-2", code: "PRJ-105", name: "Warehouse scanner rollout" }),
  ];
  const lists: URLSearchParams[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, "http://gateway.test");
      const method = init?.method ?? "GET";
      const page = Number(url.searchParams.get("page") ?? "1");
      const size = Number(url.searchParams.get("size") ?? "25");
      if (method === "GET" && url.pathname === "/api/v1/projects-workspace/members") {
        return json(200, { items: MEMBERS, total: MEMBERS.length, page, size, pages: 1 });
      }
      if (method === "GET" && url.pathname === "/api/v1/projects-workspace/projects") {
        lists.push(url.searchParams);
        return json(200, {
          items: portfolio.slice((page - 1) * size, page * size),
          total: portfolio.length,
          page,
          size,
          pages: Math.max(1, Math.ceil(portfolio.length / size)),
        });
      }
      if (method === "POST" && url.pathname === "/api/v1/projects-workspace/projects" && typeof init?.body === "string") {
        const body: ProjectIn = JSON.parse(init.body);
        const created = project({ id: `new-${body.code}`, code: body.code, name: body.name });
        portfolio.push(created);
        return json(201, created);
      }
      throw new Error(`Unexpected request: ${method} ${input}`);
    }),
  );
  return { lists: () => lists };
}

async function importFile(rows: readonly Record<string, ExcelJS.CellValue>[]): Promise<File> {
  const columns = portfolioEntity.columns.filter((column) => column.readOnly !== true);
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow(columns.map((column) => (column.required === true ? `${column.header} *` : column.header)));
  for (const row of rows) sheet.addRow(columns.map((column) => row[column.key] ?? null));
  return new File([await workbook.xlsx.writeBuffer()], "projects.xlsx", { type: XLSX_TYPE });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PortfolioPage: importing from Excel", () => {
  it("shows the projects an import created without a refresh", async () => {
    const gateway = stubGateway();
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <PermissionProvider grants={["projects.portfolio.project.write:ORG"]}>
          <PortfolioPage />
        </PermissionProvider>
      </MemoryRouter>,
    );
    await screen.findAllByText("Warehouse scanner rollout");
    expect(screen.queryAllByText("Ledger migration")).toHaveLength(0);
    expect(gateway.lists()).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Import / Export" }));
    await screen.findByRole("dialog", { name: "Import / Export projects" });
    const input = document.querySelector<HTMLInputElement>("input[type=file]");
    if (input === null) throw new Error("The dialog has no file input");
    await user.upload(
      input,
      await importFile([
        { code: "PRJ-201", name: "Ledger migration", start_date: "2026-03-02", target_date: "2026-09-30" },
      ]),
    );
    await screen.findByRole("dialog", { name: "Review your file" });
    await user.click(screen.getByRole("button", { name: "Import 1 row" }));
    await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 5_000 });
    await user.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    expect((await screen.findAllByText("Ledger migration")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Customer portal redesign").length).toBeGreaterThan(0);
    // One more request for the list, with the screen's own page, size, sort and filters.
    expect(gateway.lists()).toHaveLength(2);
    expect(Object.fromEntries(gateway.lists()[1] ?? [])).toEqual({ page: "1", size: "25", sort: "code" });
  }, 15_000);
});
