/**
 * The MRP run screen: its states (loading, error, queued and polled, failed, completed), the
 * shortage table with the supply it was netted against, the pegging drawer, the demand view, and
 * "Request requisition" — permission-gated, listing what it will send, idempotency-keyed.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { StubResponse } from "@/test-utils/stubApi";
import { problem, route, stubApi } from "@/test-utils/stubApi";
import { page } from "@/test-utils/manufacturingFixtures";
import { demand, requisition, run, shortage, shortageDetail } from "@/test-utils/mrpFixtures";
import { renderScreen } from "@/test-utils/renderScreen";
import { MrpRunDetailPage } from "./MrpRunDetail";

const READ = "operations.manufacturing.mrp.read:ORG";
const REQUEST = "operations.manufacturing.mrp.requisition:ORG";
const AT = { path: "/manufacturing/mrp/:runId", at: "/manufacturing/mrp/run-1" };

const RUN = route("GET", /\/mrp\/runs\/run-1$/, { body: run() });
const SHORTAGES = route("GET", /\/mrp\/runs\/run-1\/shortages$/, {
  body: page([
    shortage(),
    shortage({
      id: "sh-2",
      sku: "SHIRT-01",
      name: "Cotton shirt",
      uom: "pcs",
      action: "MAKE",
      level: 0,
      gross: "10",
      shortage: "10",
      need_by: null,
    }),
  ]),
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MrpRunDetailPage states", () => {
  it("shows a loading placeholder while the run is on its way", () => {
    stubApi([route("GET", /\/mrp\/runs\/run-1$/, () => new Promise<StubResponse>(() => undefined))]);
    renderScreen(<MrpRunDetailPage />, [READ], { route: AT });
    expect(screen.getByLabelText("Loading run")).toBeDefined();
  });

  it("shows the server's sentence and a retry when the run fails to load", async () => {
    stubApi([route("GET", /\/mrp\/runs\/run-1$/, problem(404, "That MRP run is not part of this organization"))]);
    renderScreen(<MrpRunDetailPage />, [READ], { route: AT });
    expect(await screen.findByText("That MRP run is not part of this organization")).toBeDefined();
    expect(screen.getByRole("button", { name: "Try again" })).toBeDefined();
  });

  it("says a failed run could not be computed, with its reason, and offers no table", async () => {
    stubApi([route("GET", /\/mrp\/runs\/run-1$/, { body: run({ status: "FAILED", error: "The bill of materials loops back on itself: A -> B -> A" }) })]);
    renderScreen(<MrpRunDetailPage />, [READ], { route: AT });
    expect(await screen.findByText("This run could not be computed")).toBeDefined();
    expect(screen.getByText(/loops back on itself/)).toBeDefined();
    expect(screen.queryByRole("button", { name: "Shortages" })).toBeNull();
  });

  it("polls a queued run and shows the result when the worker has finished it", async () => {
    let calls = 0;
    stubApi([
      route("GET", /\/mrp\/runs\/run-1$/, () => {
        calls += 1;
        return { body: calls < 3 ? run({ status: "QUEUED", execution: "WORKER", demand_count: 0, shortage_count: 0 }) : run() };
      }),
      SHORTAGES,
      route("GET", /\/shortages$/, { body: page([shortage()]) }),
    ]);
    renderScreen(<MrpRunDetailPage pollMs={20} />, [READ], { route: AT });
    expect(await screen.findByText("Waiting for the worker")).toBeDefined();
    expect(await screen.findByRole("table", { name: "Shortages" })).toBeDefined();
    expect(screen.queryByText("Waiting for the worker")).toBeNull();
    expect(calls).toBeGreaterThanOrEqual(3);
  });
});

describe("shortage table", () => {
  it("shows what is short with the supply it was netted against, as exact strings", async () => {
    stubApi([RUN, SHORTAGES]);
    renderScreen(<MrpRunDetailPage />, [READ], { route: AT });

    const table = await screen.findByRole("table", { name: "Shortages" });
    const buy = within(within(table).getByText("BTN-14MM").closest("tr") as HTMLElement);
    expect(buy.getByText("Buy")).toBeDefined();
    expect(buy.getByText("50 pcs")).toBeDefined(); // required
    expect(buy.getByText("15 pcs")).toBeDefined(); // on order
    expect(buy.getByText("35 pcs")).toBeDefined(); // shortage
    expect(buy.getByText("Not yet")).toBeDefined(); // not requisitioned
    const make = within(within(table).getByText("SHIRT-01").closest("tr") as HTMLElement);
    expect(make.getByText("Make")).toBeDefined();
  });

  it("sends the action, requisition state and search to the server", async () => {
    const api = stubApi([RUN, SHORTAGES]);
    const user = userEvent.setup();
    renderScreen(<MrpRunDetailPage />, [READ], { route: AT });
    await screen.findByRole("table", { name: "Shortages" });

    await user.click(screen.getByRole("button", { name: "Buy" }));
    await waitFor(() => expect(api.calls("GET", /shortages$/).some((r) => r.query.get("action") === "BUY")).toBe(true));
    await user.click(screen.getByRole("button", { name: "Not requested" }));
    await waitFor(() => expect(api.calls("GET", /shortages$/).some((r) => r.query.get("requested") === "false")).toBe(true));
    await user.type(screen.getByLabelText("Search shortages"), "btn");
    await waitFor(() => expect(api.calls("GET", /shortages$/).some((r) => r.query.get("q") === "btn")).toBe(true));
  });

  it("says nothing is short when the table is empty", async () => {
    stubApi([RUN, route("GET", /\/shortages$/, { body: page([]) })]);
    renderScreen(<MrpRunDetailPage />, [READ], { route: AT });
    expect(await screen.findByText("Nothing is short")).toBeDefined();
  });

  it("shows the server's sentence when the table cannot load", async () => {
    stubApi([RUN, route("GET", /\/shortages$/, problem(500, "Shortages are unavailable."))]);
    renderScreen(<MrpRunDetailPage />, [READ], { route: AT });
    expect(await screen.findByText("Shortages are unavailable.")).toBeDefined();
  });

  it("opens the pegging for a row: which demand needs it, and through which parent", async () => {
    const api = stubApi([RUN, SHORTAGES, route("GET", /\/shortages\/sh-1$/, { body: shortageDetail() })]);
    const user = userEvent.setup();
    renderScreen(<MrpRunDetailPage />, [READ], { route: AT });
    await user.click(await screen.findByText("BTN-14MM"));

    const drawer = await screen.findByRole("dialog");
    expect(await within(drawer).findByText("SO-1")).toBeDefined();
    expect(within(drawer).getByText("SHIRT-01")).toBeDefined();
    expect(within(drawer).getByText("50 pcs")).toBeDefined();
    expect(api.calls("GET", /shortages\/sh-1$/).length).toBe(1);
  });
});

describe("demand view", () => {
  it("lists what the run planned from, including what an order had already reserved", async () => {
    const api = stubApi([RUN, SHORTAGES, route("GET", /\/runs\/run-1\/demands$/, { body: page([demand()]) })]);
    const user = userEvent.setup();
    renderScreen(<MrpRunDetailPage />, [READ], { route: AT });
    await screen.findByRole("table", { name: "Shortages" });
    await user.click(screen.getByRole("button", { name: "Demand" }));

    const table = await screen.findByRole("table", { name: "Demand this run planned from" });
    const row = within(within(table).getByText("SO-1").closest("tr") as HTMLElement);
    expect(row.getByText("10 pcs")).toBeDefined(); // requested
    expect(row.getByText("2 pcs")).toBeDefined(); // already reserved
    expect(row.getByText("8 pcs")).toBeDefined(); // planned

    await user.click(screen.getByRole("button", { name: "Manual" }));
    await waitFor(() => expect(api.calls("GET", /demands$/).some((r) => r.query.get("source_type") === "MANUAL")).toBe(true));
  });
});

describe("request requisition", () => {
  const PENDING = route("GET", /\/shortages$/, (req) =>
    req.query.get("requested") === "false" && req.query.get("action") === "BUY"
      ? { body: page([shortage()]) }
      : { body: page([shortage(), shortage({ id: "sh-2", sku: "SHIRT-01", action: "MAKE", shortage: "10" })]) },
  );

  it("is hidden without the requisition grant", async () => {
    stubApi([RUN, PENDING]);
    renderScreen(<MrpRunDetailPage />, [READ], { route: AT });
    await screen.findByRole("table", { name: "Shortages" });
    expect(screen.queryByRole("button", { name: /^Request requisition/ })).toBeNull();
  });

  it("is disabled when nothing purchased is waiting", async () => {
    stubApi([RUN, route("GET", /\/shortages$/, { body: page([]) })]);
    renderScreen(<MrpRunDetailPage />, [READ, REQUEST], { route: AT });
    const button = await screen.findByRole("button", { name: "Request requisition" });
    expect(button.hasAttribute("disabled")).toBe(true);
  });

  it("lists what it will send, posts with an Idempotency-Key and reports the result", async () => {
    const api = stubApi([
      RUN,
      PENDING,
      route("POST", /\/runs\/run-1\/requisitions$/, { status: 201, body: requisition() }),
    ]);
    const user = userEvent.setup();
    renderScreen(<MrpRunDetailPage />, [READ, REQUEST], { route: AT });

    await user.click(await screen.findByRole("button", { name: "Request requisition (1)" }));
    const dialog = await screen.findByRole("dialog");
    const list = within(dialog).getByRole("list", { name: "Items to requisition" });
    expect(within(list).getByText("BTN-14MM")).toBeDefined();
    expect(within(list).getByText("35 pcs")).toBeDefined();
    expect(within(list).queryByText("SHIRT-01")).toBeNull(); // a made item is not requisitioned
    await user.type(within(dialog).getByLabelText(/Note for purchasing/), "urgent");
    await user.click(within(dialog).getByRole("button", { name: "Request requisition" }));

    await waitFor(() => expect(api.calls("POST", /requisitions$/).length).toBe(1));
    const sent = api.calls("POST", /requisitions$/)[0];
    expect(sent?.body).toEqual({ note: "urgent" });
    expect(sent?.headers["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
    expect(await screen.findByText("Requisition requested for 1 item.")).toBeDefined();
  });

  it("shows the server's refusal inside the dialog and keeps it open", async () => {
    stubApi([
      RUN,
      PENDING,
      route("POST", /\/requisitions$/, problem(409, "There is nothing left in this run to requisition")),
    ]);
    const user = userEvent.setup();
    renderScreen(<MrpRunDetailPage />, [READ, REQUEST], { route: AT });
    await user.click(await screen.findByRole("button", { name: "Request requisition (1)" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Request requisition" }));
    expect(await within(dialog).findByText("There is nothing left in this run to requisition")).toBeDefined();
  });
});
