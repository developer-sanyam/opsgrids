/**
 * `AssetsPage` and its Import / Export button: after an import that creates assets the list asks
 * the server again by itself, so the new assets show up without a refresh. The hook, the dialog
 * and the screen are all real; only the gateway is stubbed, and it holds a register that a
 * `POST /itsm/assets` adds to.
 */
import { PermissionProvider } from "@opsgrids/client";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExcelJS from "exceljs";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Asset, AssetIn } from "@/api/types";
import { assetsEntity } from "@/data/importExport/assets";

import { AssetsPage } from "./Assets";

const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function asset(over: Partial<Asset>): Asset {
  return {
    tag: "LT-0001",
    name: "Dell Latitude 7440",
    type: "laptop",
    status: "in_use",
    owner: "Anita Desai",
    location: "Bengaluru HQ",
    serial: "8H2K3Z3",
    purchased_at: "2025-08-14",
    warranty_expires_at: "2028-08-13",
    cost_minor: 8450000,
    cost_currency: "INR",
    open_incident_ids: [],
    ...over,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** A register served with real paging, which `POST /itsm/assets` appends to. */
function stubGateway() {
  const register = [
    asset({ tag: "LT-0001", name: "Dell Latitude 7440" }),
    asset({ tag: "MN-0002", name: "Dell U2723QE monitor", type: "monitor" }),
  ];
  const lists: URLSearchParams[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, "http://gateway.test");
      const method = init?.method ?? "GET";
      if (method === "GET" && url.pathname === "/api/v1/itsm/assets") {
        lists.push(url.searchParams);
        const page = Number(url.searchParams.get("page") ?? "1");
        const size = Number(url.searchParams.get("size") ?? "25");
        return json(200, {
          items: register.slice((page - 1) * size, page * size),
          total: register.length,
          page,
          size,
          pages: Math.max(1, Math.ceil(register.length / size)),
        });
      }
      if (method === "POST" && url.pathname === "/api/v1/itsm/assets" && typeof init?.body === "string") {
        const body: AssetIn = JSON.parse(init.body);
        const created = asset({ tag: body.tag, name: body.name, type: body.type });
        register.push(created);
        return json(201, created);
      }
      throw new Error(`Unexpected request: ${method} ${input}`);
    }),
  );
  return { lists: () => lists };
}

async function importFile(rows: readonly Record<string, ExcelJS.CellValue>[]): Promise<File> {
  const columns = assetsEntity.columns.filter((column) => column.readOnly !== true);
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow(columns.map((column) => (column.required === true ? `${column.header} *` : column.header)));
  for (const row of rows) sheet.addRow(columns.map((column) => row[column.key] ?? null));
  return new File([await workbook.xlsx.writeBuffer()], "assets.xlsx", { type: XLSX_TYPE });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AssetsPage: importing from Excel", () => {
  it("shows the assets an import created without a refresh", async () => {
    const gateway = stubGateway();
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <PermissionProvider grants={["itsm.cmdb.asset.write:OWN"]}>
          <AssetsPage />
        </PermissionProvider>
      </MemoryRouter>,
    );
    await screen.findAllByText("Dell U2723QE monitor");
    expect(screen.queryAllByText("Rack server")).toHaveLength(0);
    expect(gateway.lists()).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Import / Export" }));
    await screen.findByRole("dialog", { name: "Import / Export assets" });
    const input = document.querySelector<HTMLInputElement>("input[type=file]");
    if (input === null) throw new Error("The dialog has no file input");
    await user.upload(input, await importFile([{ tag: "SRV-0042", name: "Rack server", type: "Server" }]));
    await screen.findByRole("dialog", { name: "Review your file" });
    await user.click(screen.getByRole("button", { name: "Import 1 row" }));
    await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 5_000 });
    await user.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    expect((await screen.findAllByText("Rack server")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Dell Latitude 7440").length).toBeGreaterThan(0);
    // One more request for the list, with the screen's own page and filters.
    expect(gateway.lists()).toHaveLength(2);
    expect(Object.fromEntries(gateway.lists()[1] ?? [])).toEqual({ page: "1", size: "25" });
  }, 15_000);
});
