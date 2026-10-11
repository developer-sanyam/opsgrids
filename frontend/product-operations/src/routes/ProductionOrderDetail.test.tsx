/**
 * The order screen: the action bar follows the status and the caller's grants, every action sends
 * string quantities with an Idempotency-Key and replaces the order from its response, a short
 * reservation shows a "Material shortage" banner, and the server's refusals are shown verbatim.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ProductionActionOut, ProductionOrderDetailOut } from "@/api/types";
import { problem, route, stubApi } from "@/test-utils/stubApi";
import type { StubResponse } from "@/test-utils/stubApi";
import { LOCATIONS_PAGE, WAREHOUSE_PAGE, material, order, pick, version } from "@/test-utils/manufacturingFixtures";
import { renderScreen } from "@/test-utils/renderScreen";
import { ProductionOrderDetailPage } from "./ProductionOrderDetail";

const READ = "operations.manufacturing.production_order.read:ORG";
const WRITE = "operations.manufacturing.production_order.write:ORG";
const APPROVE = "operations.manufacturing.production_order.approve:ORG";
const ISSUE = "operations.manufacturing.production_order.issue:ORG";
const COMPLETE = "operations.manufacturing.production_order.complete:ORG";
const ALL = [READ, WRITE, APPROVE, ISSUE, COMPLETE];

const ROUTE = { path: "/manufacturing/orders/:orderId", at: "/manufacturing/orders/ord-1" };

afterEach(() => {
  vi.unstubAllGlobals();
});

function action(detail: ProductionOrderDetailOut, extra: Partial<ProductionActionOut> = {}): StubResponse {
  return { body: { ...detail, shortages: [], replayed: false, ...extra } };
}

function serve(detail: ProductionOrderDetailOut, ...more: ReturnType<typeof route>[]) {
  return stubApi([
    route("GET", /\/manufacturing\/orders\/ord-1$/, { body: detail }),
    route("GET", /\/operations\/warehouses$/, { body: WAREHOUSE_PAGE }),
    route("GET", /\/warehouses\/wh-1\/locations$/, { body: LOCATIONS_PAGE }),
    ...more,
  ]);
}

type Role = Parameters<typeof screen.getByRole>[0];
type RoleOptions = Parameters<typeof screen.getByRole>[1];

/** The order's action bar. `findByRole` waits for the page to load; the rest read it as it is. */
const buttons = () => {
  const bar = () => within(screen.getByRole("group", { name: "Order actions" }));
  return {
    findByRole: async (role: Role, options?: RoleOptions) =>
      within(await screen.findByRole("group", { name: "Order actions" })).findByRole(role, options),
    getByRole: (role: Role, options?: RoleOptions) => bar().getByRole(role, options),
    queryByRole: (role: Role, options?: RoleOptions) => bar().queryByRole(role, options),
    queryAllByRole: (role: Role, options?: RoleOptions) => bar().queryAllByRole(role, options),
  };
};

describe("ProductionOrderDetailPage states", () => {
  it("shows a loading placeholder while the order is on its way", () => {
    stubApi([route("GET", /\/orders\/ord-1$/, () => new Promise<StubResponse>(() => undefined))]);
    renderScreen(<ProductionOrderDetailPage />, [READ], ROUTE);
    expect(screen.getByLabelText("Loading production order")).toBeDefined();
  });

  it("shows the server's sentence and a retry when the order cannot be loaded", async () => {
    stubApi([route("GET", /\/orders\/ord-1$/, problem(404, "That production order is not part of this organization"))]);
    renderScreen(<ProductionOrderDetailPage />, [READ], ROUTE);
    expect(await screen.findByText("That production order is not part of this organization")).toBeDefined();
    expect(screen.getByRole("button", { name: "Try again" })).toBeDefined();
  });

  it("shows the empty states for an order with nothing picked, produced or wasted yet", async () => {
    serve(order());
    renderScreen(<ProductionOrderDetailPage />, [READ], ROUTE);
    expect(await screen.findByText("Nothing picked yet")).toBeDefined();
    expect(screen.getByText("No output yet")).toBeDefined();
    expect(screen.getByText("No wastage")).toBeDefined();
    expect(screen.getByText("Nothing to compare yet")).toBeDefined();
  });

  it("shows the pinned BOM version link, planned against produced, and the materials", async () => {
    serve(order({ status: "IN_PROGRESS", produced_quantity: "4" }));
    renderScreen(<ProductionOrderDetailPage />, [READ], ROUTE);
    const link = await screen.findByRole("link", { name: "SHIRT-01 · version 1" });
    expect(link.getAttribute("href")).toBe("/manufacturing/boms/bom-1/versions/ver-1");
    expect(screen.getByText("4 of 10 pc")).toBeDefined();
    expect(screen.getByText("15.375 m")).toBeDefined();
  });

  it("shows variance with the over-use word beside the signed figure", async () => {
    serve(
      order({
        status: "IN_PROGRESS",
        variance: {
          planned_quantity: "10",
          produced_quantity: "5",
          lines: [
            { material_id: "mat-1", item_id: "item-fabric", sku: "FABRIC", name: "Cotton fabric", uom: "m", standard: "7.5", theoretical: "7.3", consumed: "8", wasted: "0.5", actual: "8.5", variance: "1", variance_percent: "13.33" },
          ],
        },
      }),
    );
    renderScreen(<ProductionOrderDetailPage />, [READ], ROUTE);
    expect(await screen.findByText("Over-use")).toBeDefined();
    expect(screen.getByText("13.33%")).toBeDefined();
  });
});

describe("ProductionOrderDetailPage action bar", () => {
  it.each([
    ["DRAFT", ["Approve", "Edit", "Cancel order"], ["Start production", "Issue material", "Record output", "Complete order"]],
    ["APPROVED", ["Start production", "Reserve material", "Issue material", "Cancel order"], ["Approve", "Edit", "Record output"]],
    ["IN_PROGRESS", ["Reserve material", "Issue material", "Record output", "Complete order", "Cancel order"], ["Approve", "Start production", "Edit"]],
    ["COMPLETED", [], ["Approve", "Start production", "Reserve material", "Issue material", "Record output", "Complete order", "Cancel order", "Edit"]],
    ["CANCELLED", [], ["Approve", "Cancel order", "Edit"]],
  ] as const)("a %s order offers the actions its status allows", async (status, shown, hidden) => {
    serve(order({ status }));
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);
    await screen.findByText("PO-0001");
    for (const name of shown) expect(buttons().getByRole("button", { name })).toBeDefined();
    for (const name of hidden) expect(buttons().queryByRole("button", { name })).toBeNull();
  });

  it("offers Return material and Record wastage only while material is in WIP", async () => {
    serve(
      order({
        status: "IN_PROGRESS",
        picks: [pick({ issued: "5", wip: "5" })],
        materials: [material({ wip: "5", open_need: "0" })],
      }),
    );
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);
    await screen.findByText("PO-0001");
    expect(buttons().getByRole("button", { name: "Return material" })).toBeDefined();
    expect(buttons().getByRole("button", { name: "Record wastage" })).toBeDefined();
  });

  it("offers no action to a reader, whatever the status", async () => {
    serve(order({ status: "IN_PROGRESS", picks: [pick({ wip: "5" })] }));
    renderScreen(<ProductionOrderDetailPage />, [READ], ROUTE);
    await screen.findByText("PO-0001");
    expect(buttons().queryAllByRole("button")).toHaveLength(0);
  });

  it("holds each action to its own grant", async () => {
    serve(order({ status: "DRAFT" }));
    renderScreen(<ProductionOrderDetailPage />, [READ, APPROVE], ROUTE);
    await screen.findByText("PO-0001");
    expect(buttons().getByRole("button", { name: "Approve" })).toBeDefined();
    expect(buttons().queryByRole("button", { name: "Edit" })).toBeNull();
    expect(buttons().queryByRole("button", { name: "Cancel order" })).toBeNull();
  });
});

describe("ProductionOrderDetailPage actions", () => {
  it("approves with an Idempotency-Key and shows the order from the response without refetching", async () => {
    const api = serve(
      order({ status: "DRAFT" }),
      route("POST", /\/orders\/ord-1\/approve$/, () => action(order({ status: "APPROVED" }))),
    );
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);

    await user.click(await buttons().findByRole("button", { name: "Approve" }));

    expect(await buttons().findByRole("button", { name: "Start production" })).toBeDefined();
    expect(api.calls("POST", /approve$/)[0]?.headers["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
    expect(api.calls("GET", /orders\/ord-1$/).length).toBe(1);
  });

  it("shows a server refusal of an action verbatim", async () => {
    serve(order({ status: "APPROVED" }), route("POST", /\/start$/, problem(409, "Order PO-0001 is not approved.")));
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);
    await user.click(await buttons().findByRole("button", { name: "Start production" }));
    expect(await screen.findByText("Order PO-0001 is not approved.")).toBeDefined();
  });

  it("lists the shortages in a warning banner after a reservation that could not cover everything", async () => {
    const api = serve(
      order({ status: "APPROVED" }),
      route("POST", /\/reserve$/, () =>
        action(order({ status: "APPROVED", materials: [material({ reserved: "10", open_need: "5.375" })] }), {
          shortages: [{ material_id: "mat-1", item_id: "item-fabric", sku: "FABRIC", required: "15.375", covered: "10", short: "5.375" }],
        }),
      ),
    );
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);

    await user.click(await buttons().findByRole("button", { name: "Reserve material" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Reserve material" }));

    expect(await screen.findByText("Material shortage")).toBeDefined();
    expect(screen.getByText(/short 5.375/)).toBeDefined();
    expect(api.calls("POST", /reserve$/)[0]?.body).toEqual({});
    expect(api.calls("POST", /reserve$/)[0]?.headers["Idempotency-Key"]).toBeTruthy();
  });

  it("issues everything reserved by default, with over-issue off", async () => {
    const api = serve(
      order({ status: "APPROVED" }),
      route("POST", /\/issue$/, () => action(order({ status: "IN_PROGRESS" }))),
    );
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);

    await user.click(await buttons().findByRole("button", { name: "Issue material" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Issue material" }));

    await waitFor(() => expect(api.calls("POST", /issue$/).length).toBe(1));
    expect(api.calls("POST", /issue$/)[0]?.body).toEqual({ over_issue: false });
    expect(api.calls("POST", /issue$/)[0]?.headers["Idempotency-Key"]).toBeTruthy();
  });

  it("issues named lines with string quantities and the over-issue choice", async () => {
    const api = serve(
      order({ status: "IN_PROGRESS" }),
      route("POST", /\/issue$/, () => action(order({ status: "IN_PROGRESS" }))),
    );
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);

    await user.click(await buttons().findByRole("button", { name: "Issue material" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("radio", { name: /Choose lines/ }));
    await user.selectOptions(within(dialog).getByLabelText(/^Material/), "mat-1");
    await waitFor(() => expect(within(dialog).getByLabelText(/^Location/).hasAttribute("disabled")).toBe(false));
    await user.selectOptions(within(dialog).getByLabelText(/^Location/), "loc-1");
    await user.type(within(dialog).getByLabelText(/^Quantity/), "16.5");
    await user.click(within(dialog).getByRole("checkbox", { name: /Allow over-issue/ }));
    await user.click(within(dialog).getByRole("button", { name: "Issue material" }));

    await waitFor(() => expect(api.calls("POST", /issue$/).length).toBe(1));
    expect(api.calls("POST", /issue$/)[0]?.body).toEqual({
      lines: [{ material_id: "mat-1", location_id: "loc-1", quantity: "16.5" }],
      over_issue: true,
    });
  });

  it("shows an insufficient-stock refusal in the issue dialog and keeps what was entered", async () => {
    const api = serve(
      order({ status: "IN_PROGRESS" }),
      route("POST", /\/issue$/, problem(409, "Only 4 m of FABRIC is available at RM-1.")),
    );
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);

    await user.click(await buttons().findByRole("button", { name: "Issue material" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("checkbox", { name: /Allow over-issue/ }));
    await user.click(within(dialog).getByRole("button", { name: "Issue material" }));

    expect(await within(dialog).findByText("Only 4 m of FABRIC is available at RM-1.")).toBeDefined();
    expect((within(dialog).getByRole("checkbox", { name: /Allow over-issue/ }) as HTMLInputElement).checked).toBe(true);
    // Retrying the same submission reuses its key, so the server can replay rather than act twice.
    await user.click(within(dialog).getByRole("button", { name: /Try again|Issue material/ }));
    await waitFor(() => expect(api.calls("POST", /issue$/).length).toBe(2));
    const [first, second] = api.calls("POST", /issue$/);
    expect(first?.headers["Idempotency-Key"]).toBe(second?.headers["Idempotency-Key"]);
  });

  it("records output with a string quantity, the default location and an Idempotency-Key", async () => {
    const api = serve(
      order({ status: "IN_PROGRESS" }),
      route("POST", /\/outputs$/, () => action(order({ status: "IN_PROGRESS", produced_quantity: "4.5" }))),
    );
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);

    await user.click(await buttons().findByRole("button", { name: "Record output" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/^Quantity/), "4.5");
    await user.selectOptions(within(dialog).getByLabelText(/^Stock status/), "QUALITY_HOLD");
    await user.click(within(dialog).getByRole("button", { name: "Record output" }));

    await waitFor(() => expect(api.calls("POST", /outputs$/).length).toBe(1));
    const sent = api.calls("POST", /outputs$/)[0];
    expect(sent?.body).toEqual({ quantity: "4.5", location_id: "loc-out", status: "QUALITY_HOLD", note: "" });
    expect(sent?.headers["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
    expect(await screen.findByText("4.5 of 10 pc")).toBeDefined();
  });

  it("refuses an output quantity with too many decimals without calling the server", async () => {
    const api = serve(order({ status: "IN_PROGRESS" }));
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);
    await user.click(await buttons().findByRole("button", { name: "Record output" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/^Quantity/), "4.5555");
    await user.click(within(dialog).getByRole("button", { name: "Record output" }));
    expect(await within(dialog).findByText(/at most three decimals/)).toBeDefined();
    expect(api.calls("POST", /outputs$/).length).toBe(0);
  });

  it("records wastage with a string quantity, never offering LEFTOVER", async () => {
    const api = serve(
      order({ status: "IN_PROGRESS", picks: [pick({ wip: "5" })], materials: [material({ wip: "5" })] }),
      route("POST", /\/wastage$/, () => action(order({ status: "IN_PROGRESS" }))),
    );
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);

    await user.click(await buttons().findByRole("button", { name: "Record wastage" }));
    const dialog = await screen.findByRole("dialog");
    const types = within(dialog).getByLabelText(/^Wastage type/);
    expect(within(types).queryByRole("option", { name: "Leftover" })).toBeNull();
    expect(within(types).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Choose a type", "Cutting", "Defect", "Spillage", "Damage", "Process loss", "Other",
    ]);
    await user.selectOptions(within(dialog).getByLabelText(/^Material/), "mat-1");
    await user.selectOptions(types, "CUTTING");
    await user.type(within(dialog).getByLabelText(/^Quantity/), "0.75");
    await user.click(within(dialog).getByRole("button", { name: "Record wastage" }));

    await waitFor(() => expect(api.calls("POST", /wastage$/).length).toBe(1));
    const sent = api.calls("POST", /wastage$/)[0];
    expect(sent?.body).toEqual({ material_id: "mat-1", wastage_type: "CUTTING", quantity: "0.75", note: "" });
    expect(sent?.headers["Idempotency-Key"]).toBeTruthy();
  });

  it("returns each pick's WIP as a string quantity", async () => {
    const api = serve(
      order({ status: "IN_PROGRESS", picks: [pick({ wip: "5.5" })], materials: [material({ wip: "5.5", open_need: "0" })] }),
      route("POST", /\/returns$/, () => action(order({ status: "IN_PROGRESS" }))),
    );
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);

    await user.click(await buttons().findByRole("button", { name: "Return material" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Return material" }));

    await waitFor(() => expect(api.calls("POST", /returns$/).length).toBe(1));
    expect(api.calls("POST", /returns$/)[0]?.body).toEqual({ lines: [{ pick_id: "pick-1", quantity: "5.5" }] });
  });

  it("makes the user choose what happens to leftover WIP, and shows the server's 409 too", async () => {
    const api = serve(
      order({ status: "IN_PROGRESS", picks: [pick({ wip: "2" })] }),
      route("POST", /\/complete$/, (req) =>
        (req.body as { leftover?: string }).leftover === "return"
          ? problem(409, "2 m of FABRIC is still in WIP; choose return or scrap.")
          : action(order({ status: "COMPLETED" })),
      ),
    );
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);

    await user.click(await buttons().findByRole("button", { name: "Complete order" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Complete order" }));
    expect(await within(dialog).findByText("Choose what happens to the material left in WIP.")).toBeDefined();
    expect(api.calls("POST", /complete$/).length).toBe(0);

    await user.click(within(dialog).getByRole("radio", { name: /Return to stock/ }));
    await user.click(within(dialog).getByRole("button", { name: "Complete order" }));
    expect(await within(dialog).findByText("2 m of FABRIC is still in WIP; choose return or scrap.")).toBeDefined();
    expect(api.calls("POST", /complete$/)[0]?.body).toEqual({ note: "", leftover: "return" });

    await user.click(within(dialog).getByRole("radio", { name: /Scrap it/ }));
    await user.click(within(dialog).getByRole("button", { name: "Complete order" }));
    await waitFor(() => expect(api.calls("POST", /complete$/).length).toBe(2));
    expect(api.calls("POST", /complete$/)[1]?.body).toEqual({ note: "", leftover: "scrap" });
    await waitFor(() => expect(buttons().queryByRole("button", { name: "Complete order" })).toBeNull());
  });

  it("completes without a leftover question when nothing is in WIP", async () => {
    const api = serve(
      order({ status: "IN_PROGRESS" }),
      route("POST", /\/complete$/, () => action(order({ status: "COMPLETED" }))),
    );
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);
    await user.click(await buttons().findByRole("button", { name: "Complete order" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("radio")).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Complete order" }));
    await waitFor(() => expect(api.calls("POST", /complete$/).length).toBe(1));
    expect(api.calls("POST", /complete$/)[0]?.body).toEqual({ note: "" });
  });

  it("cancels with a reason", async () => {
    const api = serve(
      order({ status: "DRAFT" }),
      route("POST", /\/cancel$/, () => action(order({ status: "CANCELLED", cancel_reason: "Customer withdrew" }))),
    );
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);
    await user.click(await buttons().findByRole("button", { name: "Cancel order" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/^Reason/), "Customer withdrew");
    await user.click(within(dialog).getByRole("button", { name: "Cancel order" }));
    await waitFor(() => expect(api.calls("POST", /cancel$/).length).toBe(1));
    expect(api.calls("POST", /cancel$/)[0]?.body).toEqual({ reason: "Customer withdrew" });
    expect(await screen.findByText("This order was cancelled")).toBeDefined();
  });
});

describe("ProductionOrderDetailPage substitutes", () => {
  const withSubstitutes = version({
    id: "ver-1",
    components: [
      {
        id: "cmp-1", line_no: 1, component_item_id: "item-fabric", sku: "FABRIC", name: "Cotton fabric", uom: "m",
        quantity: "1.5", scrap_percent: "2.5", effective_from: null, effective_to: null, notes: "", is_produced: false,
        substitutes: [{ id: "sub-1", substitute_item_id: "item-poly", sku: "POLY", name: "Poly blend", uom: "m", ratio: "1.25", priority: 1 }],
      },
    ],
  });

  it("offers Use substitute on a draft order to a writer when the BOM line declares one", async () => {
    const api = serve(
      order({ status: "DRAFT" }),
      route("GET", /\/boms\/bom-1\/versions\/ver-1$/, { body: withSubstitutes }),
      route("POST", /\/materials\/mat-1\/substitute$/, () => ({
        body: order({ status: "DRAFT", materials: [material({ is_substitute: true, item_id: "item-poly", sku: "POLY", name: "Poly blend", ratio: "1.25" })] }),
      })),
    );
    const user = userEvent.setup();
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);

    await user.click(await screen.findByRole("button", { name: "Use substitute Material FABRIC" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("radio", { name: /POLY/ }));
    await user.click(within(dialog).getByRole("button", { name: "Use selection" }));

    await waitFor(() => expect(api.calls("POST", /substitute$/).length).toBe(1));
    expect(api.calls("POST", /substitute$/)[0]?.body).toEqual({ substitute_item_id: "item-poly" });
    expect(await screen.findByText("Substitute for FABRIC")).toBeDefined();
  });

  it("does not offer Use substitute without the write grant or once the order is running", async () => {
    serve(order({ status: "DRAFT" }), route("GET", /\/boms\/bom-1\/versions\/ver-1$/, { body: withSubstitutes }));
    const first = renderScreen(<ProductionOrderDetailPage />, [READ, APPROVE], ROUTE);
    await screen.findByText("PO-0001");
    expect(screen.queryByRole("button", { name: "Use substitute Material FABRIC" })).toBeNull();
    first.unmount();

    serve(order({ status: "IN_PROGRESS" }), route("GET", /\/boms\/bom-1\/versions\/ver-1$/, { body: withSubstitutes }));
    renderScreen(<ProductionOrderDetailPage />, ALL, ROUTE);
    await screen.findByText("PO-0001");
    expect(screen.queryByRole("button", { name: "Use substitute Material FABRIC" })).toBeNull();
  });
});
