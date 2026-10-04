/**
 * The MRP run list: its four states, the status filter and search reaching the server, the
 * "New run" gate, and the new-run form (validation before any request, string quantities, the
 * Idempotency-Key, and where the run will be computed).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { StubResponse } from "@/test-utils/stubApi";
import { problem, route, stubApi } from "@/test-utils/stubApi";
import { page } from "@/test-utils/manufacturingFixtures";
import { demandSources, run } from "@/test-utils/mrpFixtures";
import { renderScreen } from "@/test-utils/renderScreen";
import { MrpRunsPage } from "./MrpRuns";

const READ = "operations.manufacturing.mrp.read:ORG";
const RUN = "operations.manufacturing.mrp.run:ORG";

const SOURCES = route("GET", /\/mrp\/demand-sources$/, { body: demandSources() });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MrpRunsPage", () => {
  it("shows a loading placeholder while the list is on its way", () => {
    stubApi([route("GET", /\/mrp\/runs$/, () => new Promise<StubResponse>(() => undefined))]);
    renderScreen(<MrpRunsPage />, [READ]);
    expect(screen.getByLabelText("Loading runs")).toBeDefined();
  });

  it("says what to do next when there are no runs", async () => {
    stubApi([route("GET", /\/mrp\/runs$/, { body: page([]) })]);
    renderScreen(<MrpRunsPage />, [READ]);
    expect(await screen.findByText("No planning runs yet")).toBeDefined();
    expect(screen.getByText("Start a run to see what you need to buy or make.")).toBeDefined();
  });

  it("shows the server's sentence and a retry when the list fails", async () => {
    stubApi([route("GET", /\/mrp\/runs$/, problem(500, "The planner is down."))]);
    renderScreen(<MrpRunsPage />, [READ]);
    expect(await screen.findByText("The planner is down.")).toBeDefined();
    expect(screen.getByRole("button", { name: "Try again" })).toBeDefined();
  });

  it("lists each run with its status, counts and a link to its detail", async () => {
    stubApi([
      route("GET", /\/mrp\/runs$/, {
        body: page([
          run(),
          run({ id: "run-2", number: "MRP-00002", status: "QUEUED", execution: "WORKER", demand_count: 0, shortage_count: 0 }),
          run({ id: "run-3", number: "MRP-00003", status: "FAILED", error: "loop" }),
        ]),
      }),
    ]);
    renderScreen(<MrpRunsPage />, [READ]);

    const link = await screen.findByRole("link", { name: "MRP-00001" });
    expect(link.getAttribute("href")).toBe("/manufacturing/mrp/run-1");
    const done = within(link.closest("tr") as HTMLElement);
    expect(done.getByText("Completed")).toBeDefined();
    expect(done.getByText("2")).toBeDefined(); // shortages

    const queued = within(screen.getByRole("link", { name: "MRP-00002" }).closest("tr") as HTMLElement);
    expect(queued.getByText("Queued")).toBeDefined();
    expect(queued.getAllByText("—").length).toBeGreaterThan(0); // nothing computed yet
    const failed = within(screen.getByRole("link", { name: "MRP-00003" }).closest("tr") as HTMLElement);
    expect(failed.getByText("Failed")).toBeDefined();
  });

  it("sends the status filter and the search to the server", async () => {
    const api = stubApi([route("GET", /\/mrp\/runs$/, { body: page([run()]) })]);
    const user = userEvent.setup();
    renderScreen(<MrpRunsPage />, [READ]);
    await screen.findByText("MRP-00001");

    await user.click(screen.getByRole("button", { name: "Queued" }));
    await waitFor(() => expect(api.calls("GET", /runs$/).some((r) => r.query.get("status") === "QUEUED")).toBe(true));
    await user.type(screen.getByLabelText("Search runs"), "week");
    await waitFor(() => expect(api.calls("GET", /runs$/).some((r) => r.query.get("q") === "week")).toBe(true));
  });

  it("hides New run without the run grant", async () => {
    stubApi([route("GET", /\/mrp\/runs$/, { body: page([run()]) })]);
    const first = renderScreen(<MrpRunsPage />, [READ]);
    await screen.findByText("MRP-00001");
    expect(screen.queryByRole("button", { name: "New run" })).toBeNull();
    first.unmount();

    renderScreen(<MrpRunsPage />, [READ, RUN]);
    expect(await screen.findByRole("button", { name: "New run" })).toBeDefined();
  });
});

describe("new run form", () => {
  it("starts a run from the default sources with an Idempotency-Key and lets the server size it", async () => {
    const api = stubApi([
      route("GET", /\/mrp\/runs$/, { body: page([]) }),
      SOURCES,
      route("POST", /\/mrp\/runs$/, { status: 201, body: { ...run(), replayed: false } }),
    ]);
    const user = userEvent.setup();
    renderScreen(<MrpRunsPage />, [READ, RUN]);

    await user.click(await screen.findByRole("button", { name: "New run" }));
    expect(await screen.findByText(/Open now: 4 sales order lines, 2 production materials/)).toBeDefined();
    expect(await screen.findByText("It will be computed now.")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Run planning" }));

    await waitFor(() => expect(api.calls("POST", /runs$/).length).toBe(1));
    const sent = api.calls("POST", /runs$/)[0];
    expect(sent?.body).toEqual({ sources: ["SALES_ORDER", "PRODUCTION_ORDER"], mode: "auto" });
    expect(sent?.headers["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("says when a run will go to the background worker", async () => {
    stubApi([
      route("GET", /\/mrp\/runs$/, { body: page([]) }),
      route("GET", /\/mrp\/demand-sources$/, { body: demandSources({ mode_if_auto: "worker", sales_order_lines: 900 }) }),
    ]);
    const user = userEvent.setup();
    renderScreen(<MrpRunsPage />, [READ, RUN]);
    await user.click(await screen.findByRole("button", { name: "New run" }));
    expect(await screen.findByText(/lot of open demand, so it will be queued/)).toBeDefined();
  });

  it("refuses an empty selection before any request is made", async () => {
    const api = stubApi([route("GET", /\/mrp\/runs$/, { body: page([]) }), SOURCES]);
    const user = userEvent.setup();
    renderScreen(<MrpRunsPage />, [READ, RUN]);
    await user.click(await screen.findByRole("button", { name: "New run" }));
    await user.click(screen.getByRole("checkbox", { name: /Open sales orders/ }));
    await user.click(screen.getByRole("checkbox", { name: /Open production orders/ }));
    await user.click(screen.getByRole("button", { name: "Run planning" }));

    expect(await screen.findByText("Choose at least one place to plan from.")).toBeDefined();
    expect(api.calls("POST", /runs$/).length).toBe(0);
  });

  it("sends manual quantities as the typed strings and forces the worker when asked", async () => {
    const api = stubApi([
      route("GET", /\/mrp\/runs$/, { body: page([]) }),
      SOURCES,
      route("GET", /\/operations\/inventory$/, {
        body: page([{ id: "item-shirt", sku: "SHIRT-01", name: "Cotton shirt" }]),
      }),
      route("POST", /\/mrp\/runs$/, {
        status: 202,
        body: { ...run({ status: "QUEUED", execution: "WORKER" }), replayed: false },
      }),
    ]);
    const user = userEvent.setup();
    renderScreen(<MrpRunsPage />, [READ, RUN]);
    await user.click(await screen.findByRole("button", { name: "New run" }));

    await user.click(screen.getByRole("checkbox", { name: /Manual demand/ }));
    await user.type(screen.getByRole("combobox", { name: "Item 1" }), "shirt");
    await user.click(await screen.findByRole("option", { name: /SHIRT-01/ }));
    await user.type(screen.getAllByLabelText(/^Quantity/)[0] as HTMLElement, "12.500");
    await user.click(screen.getByRole("switch", { name: "Run in the background" }));
    await user.click(screen.getByRole("button", { name: "Run planning" }));

    await waitFor(() => expect(api.calls("POST", /runs$/).length).toBe(1));
    const body = api.calls("POST", /runs$/)[0]?.body as { manual_demands: { quantity: unknown }[]; mode: string };
    expect(body.manual_demands).toEqual([{ item_id: "item-shirt", quantity: "12.500" }]);
    expect(typeof body.manual_demands[0]?.quantity).toBe("string");
    expect(body.mode).toBe("worker");
  });

  it("flags a bad manual quantity on its own field and sends nothing", async () => {
    const api = stubApi([
      route("GET", /\/mrp\/runs$/, { body: page([]) }),
      SOURCES,
      route("GET", /\/operations\/inventory$/, { body: page([{ id: "item-shirt", sku: "SHIRT-01", name: "Cotton shirt" }]) }),
    ]);
    const user = userEvent.setup();
    renderScreen(<MrpRunsPage />, [READ, RUN]);
    await user.click(await screen.findByRole("button", { name: "New run" }));
    await user.click(screen.getByRole("checkbox", { name: /Manual demand/ }));
    await user.type(screen.getAllByLabelText(/^Quantity/)[0] as HTMLElement, "1.2345");
    await user.click(screen.getByRole("button", { name: "Run planning" }));

    expect(await screen.findByText("Choose an item.")).toBeDefined();
    expect(screen.getByText(/at most three decimals/)).toBeDefined();
    expect(api.calls("POST", /runs$/).length).toBe(0);
  });

  it("shows the server's refusal inside the dialog", async () => {
    stubApi([
      route("GET", /\/mrp\/runs$/, { body: page([]) }),
      SOURCES,
      route("POST", /\/mrp\/runs$/, problem(409, "The bill of materials loops back on itself: A -> B -> A")),
    ]);
    const user = userEvent.setup();
    renderScreen(<MrpRunsPage />, [READ, RUN]);
    await user.click(await screen.findByRole("button", { name: "New run" }));
    await user.click(screen.getByRole("button", { name: "Run planning" }));
    expect(await screen.findByText(/loops back on itself/)).toBeDefined();
  });
});
