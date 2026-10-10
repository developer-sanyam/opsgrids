/**
 * The BOM list: its four states, the "New BOM" permission gate, and the create call carrying a
 * string quantity and an Idempotency-Key.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { StubResponse } from "@/test-utils/stubApi";
import { problem, route, stubApi } from "@/test-utils/stubApi";
import { bom, page } from "@/test-utils/manufacturingFixtures";
import { renderScreen } from "@/test-utils/renderScreen";
import { BomsPage } from "./Boms";

const READ = "operations.manufacturing.bom.read:ORG";
const WRITE = "operations.manufacturing.bom.write:ORG";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BomsPage", () => {
  it("shows a loading placeholder while the list is on its way", () => {
    stubApi([route("GET", /\/manufacturing\/boms$/, () => new Promise<StubResponse>(() => undefined))]);
    renderScreen(<BomsPage />, [READ]);
    expect(screen.getByLabelText("Loading BOMs")).toBeDefined();
  });

  it("says what to do next when there are no BOMs", async () => {
    stubApi([route("GET", /\/manufacturing\/boms$/, { body: page([]) })]);
    renderScreen(<BomsPage />, [READ]);
    expect(await screen.findByText("No BOMs yet")).toBeDefined();
    expect(screen.getByText("Create a BOM to define what goes into a produced item.")).toBeDefined();
  });

  it("shows the server's sentence and a retry when the list fails", async () => {
    const api = stubApi([route("GET", /\/manufacturing\/boms$/, problem(500, "The database is unavailable."))]);
    const user = userEvent.setup();
    renderScreen(<BomsPage />, [READ]);
    expect(await screen.findByText("The database is unavailable.")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(api.calls("GET", /\/boms$/).length).toBe(2));
  });

  it("lists each BOM with its item, current version and version count", async () => {
    stubApi([
      route("GET", /\/manufacturing\/boms$/, {
        body: page([bom(), bom({ id: "bom-2", code: "COAT-01", name: "Wool coat", current_version_no: null, current_version_id: null, version_count: 1, is_active: false })]),
      }),
    ]);
    renderScreen(<BomsPage />, [READ]);
    expect(await screen.findByText("SHIRT-01")).toBeDefined();
    expect(screen.getByText("Version 1")).toBeDefined();
    expect(screen.getByText("Nothing published")).toBeDefined();
    expect(screen.getByRole("link", { name: "COAT-01" }).getAttribute("href")).toBe("/manufacturing/boms/bom-2");
    expect(screen.getByText("Inactive")).toBeDefined();
  });

  it("passes the search through to the server", async () => {
    const api = stubApi([route("GET", /\/manufacturing\/boms$/, { body: page([bom()]) })]);
    const user = userEvent.setup();
    renderScreen(<BomsPage />, [READ]);
    await screen.findByText("SHIRT-01");
    await user.type(screen.getByLabelText("Search BOMs"), "coat");
    await waitFor(() => expect(api.calls("GET", /\/boms$/).some((r) => r.query.get("q") === "coat")).toBe(true));
  });

  it("hides New BOM without the write grant and offers it with it", async () => {
    stubApi([route("GET", /\/manufacturing\/boms$/, { body: page([bom()]) })]);
    const first = renderScreen(<BomsPage />, [READ]);
    await screen.findByText("SHIRT-01");
    expect(screen.queryByRole("button", { name: "New BOM" })).toBeNull();
    first.unmount();

    renderScreen(<BomsPage />, [READ, WRITE]);
    expect(await screen.findByRole("button", { name: "New BOM" })).toBeDefined();
  });

  it("creates a BOM with a string quantity and an Idempotency-Key", async () => {
    const api = stubApi([
      route("GET", /\/manufacturing\/boms$/, { body: page([]) }),
      route("GET", /\/operations\/inventory$/, {
        body: page([{ id: "item-shirt", sku: "SHIRT", name: "Shirt" }]),
      }),
      route("POST", /\/manufacturing\/boms$/, { status: 201, body: { ...bom(), versions: [] } }),
    ]);
    const user = userEvent.setup();
    renderScreen(<BomsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: "New BOM" }));
    await user.type(screen.getByLabelText(/^Code/), "SHIRT-01");
    await user.type(screen.getByLabelText(/^Name/), "Cotton shirt");
    await user.type(screen.getByRole("combobox", { name: "Produced item" }), "shirt");
    await user.click(await screen.findByRole("option", { name: /SHIRT/ }));
    const quantity = screen.getByLabelText(/^Output quantity/);
    await user.clear(quantity);
    await user.type(quantity, "10.5");
    await user.click(screen.getByRole("button", { name: "Create BOM" }));

    await waitFor(() => expect(api.calls("POST", /\/boms$/).length).toBe(1));
    const sent = api.calls("POST", /\/boms$/)[0];
    expect(sent?.body).toEqual({
      code: "SHIRT-01",
      name: "Cotton shirt",
      item_id: "item-shirt",
      description: "",
      output_quantity: "10.5",
    });
    expect(sent?.headers["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("refuses a quantity with too many decimals before any request is made", async () => {
    const api = stubApi([route("GET", /\/manufacturing\/boms$/, { body: page([]) })]);
    const user = userEvent.setup();
    renderScreen(<BomsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: "New BOM" }));
    await user.type(screen.getByLabelText(/^Code/), "A1");
    await user.type(screen.getByLabelText(/^Name/), "A");
    const quantity = screen.getByLabelText(/^Output quantity/);
    await user.clear(quantity);
    await user.type(quantity, "1.2345");
    await user.click(screen.getByRole("button", { name: "Create BOM" }));

    expect(await screen.findByText("Enter a number with at most three decimals, like 82.5.")).toBeDefined();
    expect(api.calls("POST", /\/boms$/).length).toBe(0);
  });
});
