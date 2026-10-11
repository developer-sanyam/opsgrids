/**
 * One BOM and its versions: the table, the states around it, and "New version" — gated on the
 * write grant, copying an earlier version, and refused with the server's sentence when a draft
 * already exists.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { BomDetailOut, BomVersionOut } from "@/api/types";
import type { StubResponse } from "@/test-utils/stubApi";
import { problem, route, stubApi } from "@/test-utils/stubApi";
import { bom, version } from "@/test-utils/manufacturingFixtures";
import { renderScreen } from "@/test-utils/renderScreen";
import { BomDetailPage } from "./BomDetail";

const READ = "operations.manufacturing.bom.read:ORG";
const WRITE = "operations.manufacturing.bom.write:ORG";
const ROUTE = { path: "/manufacturing/boms/:bomId", at: "/manufacturing/boms/bom-1" };

const { components: _components, ...v2 } = version();
void _components;
const VERSIONS: BomVersionOut[] = [
  { ...v2, id: "ver-2", version_no: 2, status: "DRAFT" },
  { ...v2, id: "ver-1", version_no: 1, status: "PUBLISHED", published_at: "2026-09-10T08:00:00Z", content_hash: "h1", effective_from: "2026-09-10" },
];
const DETAIL: BomDetailOut = { ...bom(), versions: VERSIONS };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BomDetailPage", () => {
  it("shows a loading placeholder, then the versions", async () => {
    stubApi([route("GET", /\/boms\/bom-1$/, () => new Promise<StubResponse>(() => undefined))]);
    renderScreen(<BomDetailPage />, [READ], ROUTE);
    expect(screen.getByLabelText("Loading BOM")).toBeDefined();
  });

  it("shows the server's sentence when the BOM cannot be loaded", async () => {
    stubApi([route("GET", /\/boms\/bom-1$/, problem(404, "That BOM is not part of this organization"))]);
    renderScreen(<BomDetailPage />, [READ], ROUTE);
    expect(await screen.findByText("That BOM is not part of this organization")).toBeDefined();
  });

  it("lists versions with their status, component count and a link to each", async () => {
    stubApi([route("GET", /\/boms\/bom-1$/, { body: DETAIL })]);
    renderScreen(<BomDetailPage />, [READ], ROUTE);

    const published = await screen.findByRole("link", { name: /^Version 1/ });
    expect(published.getAttribute("href")).toBe("/manufacturing/boms/bom-1/versions/ver-1");
    const row = published.closest("tr") as HTMLElement;
    expect(within(row).getByText("Published")).toBeDefined();
    expect(within(row).getByText("In force")).toBeDefined();
    expect(screen.getByText("Draft")).toBeDefined();
    expect(screen.getByText("Version 1 is the one in force today.")).toBeDefined();
  });

  it("says what to do next when a BOM has no versions", async () => {
    stubApi([route("GET", /\/boms\/bom-1$/, { body: { ...DETAIL, versions: [], current_version_id: null, current_version_no: null } })]);
    renderScreen(<BomDetailPage />, [READ], ROUTE);
    expect(await screen.findByText("No versions yet")).toBeDefined();
    expect(screen.getByText(/Nothing is published yet/)).toBeDefined();
  });

  it("hides New version without the write grant", async () => {
    stubApi([route("GET", /\/boms\/bom-1$/, { body: DETAIL })]);
    renderScreen(<BomDetailPage />, [READ], ROUTE);
    await screen.findByText("Draft");
    expect(screen.queryByRole("button", { name: "New version" })).toBeNull();
  });

  it("creates a version copied from an earlier one, with a string batch size and an Idempotency-Key", async () => {
    const api = stubApi([
      route("GET", /\/boms\/bom-1$/, { body: DETAIL }),
      route("POST", /\/boms\/bom-1\/versions$/, { status: 201, body: version({ id: "ver-3", version_no: 3 }) }),
    ]);
    const user = userEvent.setup();
    renderScreen(<BomDetailPage />, [READ, WRITE], ROUTE);

    await user.click(await screen.findByRole("button", { name: "New version" }));
    await user.selectOptions(screen.getByLabelText("Copy from"), "ver-1");
    const size = screen.getByLabelText(/^Batch size/);
    await user.clear(size);
    await user.type(size, "12.5");
    await user.click(screen.getByRole("button", { name: "Create draft" }));

    await waitFor(() => expect(api.calls("POST", /versions$/).length).toBe(1));
    const sent = api.calls("POST", /versions$/)[0];
    expect(sent?.body).toEqual({ output_quantity: "12.5", notes: "", copy_from_version_id: "ver-1" });
    expect(sent?.headers["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("shows the server's 409 when a draft already exists, and keeps what was typed", async () => {
    stubApi([
      route("GET", /\/boms\/bom-1$/, { body: DETAIL }),
      route("POST", /\/boms\/bom-1\/versions$/, problem(409, "SHIRT-01 already has a draft version (2).")),
    ]);
    const user = userEvent.setup();
    renderScreen(<BomDetailPage />, [READ, WRITE], ROUTE);

    await user.click(await screen.findByRole("button", { name: "New version" }));
    const size = screen.getByLabelText(/^Batch size/);
    await user.clear(size);
    await user.type(size, "8");
    await user.click(screen.getByRole("button", { name: "Create draft" }));

    expect(await screen.findByText("SHIRT-01 already has a draft version (2).")).toBeDefined();
    expect((screen.getByLabelText(/^Batch size/) as HTMLInputElement).value).toBe("8");
  });
});
