/**
 * Fixture mode (`VITE_USE_FIXTURES=true`, dev builds only) has to fail the way the server does. The
 * Excel importer reads a 409 from an `ApiError` as "already exists" and a 422 as a row the server
 * refused; a plain `Error` there would be reported as a dropped connection. The flag is read once
 * when the client module loads, so each test loads a fresh copy of it with the flag on.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

async function fixtureMode() {
  vi.stubEnv("VITE_USE_FIXTURES", "true");
  vi.resetModules();
  const [client, fixtures, { ApiError }] = await Promise.all([
    import("./client"),
    import("./fixtures"),
    import("@opsgrids/client"),
  ]);
  return { client, fixtures, ApiError };
}

/** What a create rejects with, so a test can look at the error itself. */
async function refusal(attempt: () => Promise<unknown>): Promise<unknown> {
  try {
    await attempt();
  } catch (error) {
    return error;
  }
  throw new Error("the create succeeded, and should have been refused");
}

describe("a fixture create that the server would refuse", () => {
  it("answers a supplier code that exists with a 409 carrying the server's sentence and its duplicate field", async () => {
    const { client, fixtures, ApiError } = await fixtureMode();
    const existing = fixtures.SUPPLIERS[0];
    if (existing === undefined) throw new Error("no fixture supplier");

    const error = await refusal(() => client.createSupplier({ code: existing.code, name: "Again", category: "mro" }));

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      message: `A supplier coded '${existing.code}' already exists`,
      problem: {
        status: 409,
        title: "Conflict",
        detail: `A supplier coded '${existing.code}' already exists`,
        errors: [{ field: "code", code: "duplicate", message: `A supplier coded '${existing.code}' already exists` }],
      },
    });
  });

  it("answers an inventory SKU that exists with a 409, and a warehouse that does not with a 422", async () => {
    const { client, fixtures, ApiError } = await fixtureMode();
    const item = fixtures.INVENTORY[0];
    const warehouse = fixtures.WAREHOUSES[0];
    if (item === undefined || warehouse === undefined) throw new Error("no fixture inventory");
    const input = { sku: item.sku, name: "Again", category: item.category, warehouse: warehouse.name, uom: "pcs" };

    const duplicate = await refusal(() => client.createInventoryItem(input));
    expect(duplicate).toBeInstanceOf(ApiError);
    expect(duplicate).toMatchObject({
      status: 409,
      message: `An inventory item with SKU '${item.sku}' already exists`,
    });

    const unknown = await refusal(() => client.createInventoryItem({ ...input, sku: "NEW-SKU", warehouse: "Atlantis" }));
    expect(unknown).toBeInstanceOf(ApiError);
    expect(unknown).toMatchObject({
      status: 422,
      message: "'Atlantis' does not match an existing warehouse",
      problem: { status: 422, title: "Validation failed" },
    });
  });

  it("answers a category code that exists with a 409, and a parent that does not with a 422", async () => {
    const { client, fixtures, ApiError } = await fixtureMode();
    const category = fixtures.ITEM_CATEGORIES[0];
    if (category === undefined) throw new Error("no fixture category");

    const duplicate = await refusal(() => client.createItemCategory({ code: category.code, name: "Again" }));
    expect(duplicate).toBeInstanceOf(ApiError);
    expect(duplicate).toMatchObject({
      status: 409,
      message: `A category coded '${category.code}' already exists`,
      problem: { errors: [{ field: "code", code: "duplicate" }] },
    });

    const orphan = await refusal(() => client.createItemCategory({ code: "NEW", name: "New", parent_id: "gone" }));
    expect(orphan).toBeInstanceOf(ApiError);
    expect(orphan).toMatchObject({ status: 422, message: "parent_id does not match an existing category" });
  });

  it("answers a warehouse code or name that exists with a 409 naming the field", async () => {
    const { client, fixtures, ApiError } = await fixtureMode();
    const warehouse = fixtures.WAREHOUSES[0];
    if (warehouse === undefined) throw new Error("no fixture warehouse");

    const byCode = await refusal(() => client.createWarehouse({ code: warehouse.code, name: "Fresh name" }));
    expect(byCode).toBeInstanceOf(ApiError);
    expect(byCode).toMatchObject({
      status: 409,
      message: `A warehouse coded '${warehouse.code}' already exists`,
      problem: { errors: [{ field: "code", code: "duplicate" }] },
    });

    const byName = await refusal(() => client.createWarehouse({ code: "WH-FRESH", name: warehouse.name }));
    expect(byName).toBeInstanceOf(ApiError);
    expect(byName).toMatchObject({
      status: 409,
      message: `A warehouse named '${warehouse.name}' already exists`,
      problem: { errors: [{ field: "name", code: "duplicate" }] },
    });
  });

  it("still creates what the server would accept", async () => {
    const { client, fixtures } = await fixtureMode();
    const parent = fixtures.ITEM_CATEGORIES[0];
    if (parent === undefined) throw new Error("no fixture category");

    const created = await client.createItemCategory({ code: "NEW-CODE", name: "New", parent_id: parent.id });

    expect(created).toMatchObject({ code: "NEW-CODE", parent_id: parent.id });
  });

  it("accepts a code, SKU or name that differs from an existing one only in capitals, since the server compares them exactly", async () => {
    const { client, fixtures } = await fixtureMode();
    const [supplier, item, category, warehouse] = [
      fixtures.SUPPLIERS[0],
      fixtures.INVENTORY[0],
      fixtures.ITEM_CATEGORIES[0],
      fixtures.WAREHOUSES[0],
    ];
    if (!supplier || !item || !category || !warehouse) throw new Error("no fixtures");
    for (const text of [supplier.code, item.sku, category.code, warehouse.code, warehouse.name]) {
      expect(text.toLowerCase(), "the fixture text has capitals to vary").not.toBe(text);
    }

    expect(await client.createSupplier({ code: supplier.code.toLowerCase(), name: "Again", category: "mro" })).toMatchObject({
      code: supplier.code.toLowerCase(),
    });
    expect(
      await client.createInventoryItem({
        sku: item.sku.toLowerCase(),
        name: "Again",
        category: item.category,
        warehouse: warehouse.name,
        uom: "pcs",
      }),
    ).toMatchObject({ sku: item.sku.toLowerCase() });
    expect(await client.createItemCategory({ code: category.code.toLowerCase(), name: "Again" })).toMatchObject({
      code: category.code.toLowerCase(),
    });
    expect(
      await client.createWarehouse({ code: warehouse.code.toLowerCase(), name: warehouse.name.toLowerCase() }),
    ).toMatchObject({ code: warehouse.code.toLowerCase(), name: warehouse.name.toLowerCase() });
  });
});

/** The bulk update sends a PATCH per row and reads a 409 or a 422 from an `ApiError` as that row's
 *  failure, so an update refused by the server has to be refused the same way here. */
describe("a fixture update that the server would refuse", () => {
  it("answers an inventory item moved to a warehouse that does not exist with a 422", async () => {
    const { client, fixtures, ApiError } = await fixtureMode();
    const item = fixtures.INVENTORY[0];
    if (item === undefined) throw new Error("no fixture inventory");

    const error = await refusal(() => client.updateInventoryItem(item.id, { warehouse: "Atlantis" }));

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 422,
      message: "'Atlantis' does not match an existing warehouse",
      problem: { status: 422, title: "Validation failed" },
    });
  });

  it("answers a warehouse renamed to another warehouse's name with a 409 naming the field", async () => {
    const { client, fixtures, ApiError } = await fixtureMode();
    const [warehouse, other] = fixtures.WAREHOUSES;
    if (warehouse === undefined || other === undefined) throw new Error("no fixture warehouses");

    const error = await refusal(() => client.updateWarehouse(warehouse.id, { name: other.name }));

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      message: `A warehouse named '${other.name}' already exists`,
      problem: { errors: [{ field: "name", code: "duplicate" }] },
    });
  });

  it("accepts a rename to another warehouse's name in other capitals, since the server compares names exactly", async () => {
    const { client, fixtures } = await fixtureMode();
    const [warehouse, other] = fixtures.WAREHOUSES;
    if (warehouse === undefined || other === undefined) throw new Error("no fixture warehouses");

    const renamed = await client.updateWarehouse(warehouse.id, { name: other.name.toUpperCase() });

    expect(renamed).toMatchObject({ id: warehouse.id, name: other.name.toUpperCase() });
  });

  it("answers a different unit of measure on an item that has stock history with a 409, as the server does", async () => {
    const { client, fixtures, ApiError } = await fixtureMode();
    const item = fixtures.INVENTORY[0];
    if (item === undefined) throw new Error("no fixture inventory");
    expect(fixtures.MOVEMENTS[item.id]?.length, "the fixture item has stock history").toBeGreaterThan(0);

    const error = await refusal(() => client.updateInventoryItem(item.id, { uom: `${item.uom}-other` }));

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      message: `${item.sku} already has stock history in '${item.uom}'; its unit of measure cannot change once any stock has moved`,
      problem: { status: 409, title: "Conflict" },
    });
  });

  it("leaves a unit alone that is spelled as before in other capitals, and changes one on an item with no history", async () => {
    const { client, fixtures } = await fixtureMode();
    const [item, other] = fixtures.INVENTORY;
    if (item === undefined || other === undefined) throw new Error("no fixture inventory");

    expect(await client.updateInventoryItem(item.id, { uom: item.uom.toUpperCase() })).toMatchObject({ id: item.id });
    fixtures.MOVEMENTS[other.id] = [];
    expect(await client.updateInventoryItem(other.id, { uom: `${other.uom}-other` })).toMatchObject({
      uom: `${other.uom}-other`,
    });
  });

  it("answers a category given a parent that is missing, is itself, or sits below it with a 422", async () => {
    const { client, fixtures, ApiError } = await fixtureMode();
    const [metals, , , steel] = fixtures.ITEM_CATEGORIES;
    if (metals === undefined || steel === undefined) throw new Error("no fixture categories");
    expect(steel.parent_id).toBe(metals.id);

    const missing = await refusal(() => client.updateItemCategory(steel.id, { parent_id: "gone" }));
    expect(missing).toBeInstanceOf(ApiError);
    expect(missing).toMatchObject({ status: 422, message: "parent_id does not match an existing category" });

    const own = await refusal(() => client.updateItemCategory(steel.id, { parent_id: steel.id }));
    expect(own).toMatchObject({ status: 422, message: "A category cannot be its own parent" });

    const loop = await refusal(() => client.updateItemCategory(metals.id, { parent_id: steel.id }));
    expect(loop).toMatchObject({ status: 422, message: "That would make the category tree circular" });
  });

  it("still applies what the server would accept: a free name, a warehouse that exists, a new parent, no parent", async () => {
    const { client, fixtures } = await fixtureMode();
    const [warehouse, other] = fixtures.WAREHOUSES;
    const [metals, polymers, , steel] = fixtures.ITEM_CATEGORIES;
    const item = fixtures.INVENTORY[0];
    if (!warehouse || !other || !metals || !polymers || !steel || !item) throw new Error("no fixtures");

    expect(await client.updateWarehouse(warehouse.id, { name: "Renamed Hub" })).toMatchObject({ name: "Renamed Hub" });
    expect(await client.updateInventoryItem(item.id, { warehouse: other.name })).toMatchObject({ warehouse: other.name });
    expect(await client.updateItemCategory(steel.id, { parent_id: polymers.id })).toMatchObject({ parent_id: polymers.id });
    expect(await client.updateItemCategory(steel.id, { parent_id: null })).toMatchObject({ parent_id: null });
    expect(await client.updateItemCategory(metals.id, { parent_id: polymers.id })).toMatchObject({ parent_id: polymers.id });
  });
});

/**
 * The movement screens (inventory Phase 3) in fixture mode: every refusal the server makes is an
 * `ApiError` carrying its problem document, so the same screen code handles both. Quantities stay
 * decimal strings throughout.
 */
describe("fixture movements refuse what the server would refuse", () => {
  async function movements() {
    const { client, fixtures, ApiError } = await fixtureMode();
    const movementClient = await import("./movements");
    const warehouse = fixtures.WAREHOUSES[0];
    const bin = warehouse ? fixtures.LOCATIONS[warehouse.id]?.find((l) => l.kind === "bin") : undefined;
    const item = fixtures.INVENTORY[0];
    if (!warehouse || !bin || !item) throw new Error("no fixtures");
    return { client, fixtures, ApiError, m: movementClient, warehouse, bin, item };
  }

  it("answers a reservation larger than what is available with an insufficient-stock 409 carrying the numbers", async () => {
    const { m, bin, item, ApiError } = await movements();
    const error = await refusal(() =>
      m.createReservation(
        { inventory_item_id: item.id, location_id: bin.id, quantity: "100000.5", reference_type: "manual" },
        "k1",
      ),
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      problem: {
        type: "https://errors.opsgrids.dev/insufficient-stock",
        errors: [
          { sku: item.sku, requested: "100000.5", on_hand: item.on_hand_qty, available: item.available_qty, reservations: [] },
        ],
      },
    });
  });

  it("answers a reservation for an order with no order id with a 422 on reference_id, and a lapsed expiry with one on expires_at", async () => {
    const { m, bin, item } = await movements();
    const missing = await refusal(() =>
      m.createReservation({ inventory_item_id: item.id, location_id: bin.id, quantity: "1", reference_type: "order" }, "k"),
    );
    expect(missing).toMatchObject({ status: 422, problem: { errors: [{ field: "reference_id" }] } });
    const lapsed = await refusal(() =>
      m.createReservation(
        { inventory_item_id: item.id, location_id: bin.id, quantity: "1", expires_at: "2000-01-01T00:00:00Z" },
        "k",
      ),
    );
    expect(lapsed).toMatchObject({ status: 422, problem: { errors: [{ field: "expires_at" }] } });
  });

  it("holds stock on a reservation and gives part, then all, of it back", async () => {
    const { m, bin, item } = await movements();
    const made = await m.createReservation(
      { inventory_item_id: item.id, location_id: bin.id, quantity: "10.5", reference_type: "manual" },
      "k",
    );
    expect(made).toMatchObject({ quantity: "10.5", remaining: "10.5", status: "active" });

    const tooMuch = await refusal(() => m.releaseReservation(made.id, { quantity: "11" }, "k"));
    expect(tooMuch).toMatchObject({ status: 409 });

    expect(await m.releaseReservation(made.id, { quantity: "0.5" }, "k")).toMatchObject({
      remaining: "10",
      status: "active",
    });
    expect(await m.releaseReservation(made.id, {}, "k")).toMatchObject({ remaining: "0", status: "released" });
    const again = await refusal(() => m.releaseReservation(made.id, {}, "k"));
    expect(again).toMatchObject({ status: 409 });
  });

  it("answers an adjustment at the threshold with no evidence with a 422 on evidence_document_id, and holds it with one", async () => {
    const { m, bin, item } = await movements();
    const input = { inventory_item_id: item.id, location_id: bin.id, quantity_delta: "-50", reason: "lost" as const };
    const error = await refusal(() => m.createAdjustment(input, "k"));
    expect(error).toMatchObject({ status: 422, problem: { errors: [{ field: "evidence_document_id" }] } });

    const held = await m.createAdjustment({ ...input, evidence_document_id: "0b8f3a52-6f1d-4e0c-8a3e-3b7c5d9e1f20" }, "k");
    expect(held).toMatchObject({ status: "PENDING_APPROVAL", approval_required: true });
    expect(held.number).toMatch(/^ADJ-\d{6}$/);
  });

  it("applies an adjustment under the threshold at once, and refuses to cancel what is no longer pending", async () => {
    const { m, bin, item } = await movements();
    const applied = await m.createAdjustment(
      { inventory_item_id: item.id, location_id: bin.id, quantity_delta: "-1.5", reason: "damaged" },
      "k",
    );
    expect(applied).toMatchObject({ status: "APPLIED", quantity_delta: "-1.5" });
    const error = await refusal(() => m.cancelAdjustment(applied.id, {}));
    expect(error).toMatchObject({ status: 409 });
  });

  it("withdraws a pending adjustment", async () => {
    const { m } = await movements();
    const pending = (await m.listAdjustments({ status: "PENDING_APPROVAL" })).items[0];
    if (!pending) throw new Error("no pending fixture adjustment");
    expect(await m.cancelAdjustment(pending.id, { reason: "mistake" })).toMatchObject({ status: "CANCELLED" });
  });

  it("answers an unknown transfer with a 404", async () => {
    const { m, ApiError } = await movements();
    const error = await refusal(() => m.getTransfer("nope"));
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404 });
  });

  it("refuses a transfer between a warehouse and itself, and acts on a transfer only from the states the server allows", async () => {
    const { m, warehouse, bin, item } = await movements();
    const same = await refusal(() =>
      m.createTransfer(
        {
          from_warehouse_id: warehouse.id,
          to_warehouse_id: warehouse.id,
          lines: [{ inventory_item_id: item.id, from_location_id: bin.id, quantity: "1" }],
        },
        "k",
      ),
    );
    expect(same).toMatchObject({ status: 422 });

    const draft = (await m.listTransfers({ status: "DRAFT" })).items[0];
    if (!draft) throw new Error("no draft fixture transfer");
    expect(await refusal(() => m.dispatchTransfer(draft.id, {}, "k"))).toMatchObject({ status: 409 });
    expect(await refusal(() => m.receiveTransfer(draft.id, { lines: [] }, "k"))).toMatchObject({ status: 409 });

    const inTransit = (await m.listTransfers({ status: "IN_TRANSIT" })).items[0];
    if (!inTransit) throw new Error("no in-transit fixture transfer");
    expect(await refusal(() => m.cancelTransfer(inTransit.id, { reason: "x" }))).toMatchObject({
      status: 409,
      message: "The stock is in transit; receive it, or declare what is short",
    });
  });

  it("will not let the requester approve their own transfer, and wants a reason for a rejection", async () => {
    const { m } = await movements();
    await m.putControlSettings({ transfer_approval_threshold: null, adjustment_approval_threshold: null });
    const draft = (await m.listTransfers({ status: "DRAFT" })).items[0];
    if (!draft) throw new Error("no draft fixture transfer");
    const requested = await m.requestTransfer(draft.id);
    expect(requested).toMatchObject({ status: "REQUESTED", approval_route: "DIRECT" });

    expect(await refusal(() => m.approveTransfer(draft.id))).toMatchObject({
      status: 409,
      message: "The person who requested a transfer cannot approve or reject it",
    });

    const other = (await m.listTransfers({ status: "REQUESTED" })).items.find((t) => t.id !== draft.id);
    if (!other) throw new Error("no second requested fixture transfer");
    expect(await refusal(() => m.rejectTransfer(other.id, { comment: "  " }))).toMatchObject({
      status: 422,
      problem: { errors: [{ field: "comment" }] },
    });
  });

  it("routes a request by the threshold: under it is approved at once, over it waits for the approval service", async () => {
    const { m } = await movements();
    const draft = (await m.listTransfers({ status: "DRAFT" })).items[0];
    if (!draft) throw new Error("no draft fixture transfer");
    const full = await m.getTransfer(draft.id);
    // The seeded draft moves 250 against a 500 threshold.
    expect(await m.requestTransfer(draft.id)).toMatchObject({ status: "APPROVED", approval_route: "AUTO" });

    await m.putControlSettings({ transfer_approval_threshold: "100", adjustment_approval_threshold: "50" });
    const second = await m.createTransfer(
      {
        from_warehouse_id: full.from_warehouse.id,
        to_warehouse_id: full.to_warehouse.id,
        lines: full.lines.map((l) => ({
          inventory_item_id: l.inventory_item_id,
          from_location_id: l.from_location_id,
          quantity: "100",
        })),
      },
      "k",
    );
    expect(await m.requestTransfer(second.id)).toMatchObject({
      status: "REQUESTED",
      approval_route: "SERVICE",
      awaiting_approval: true,
    });
  });

  it("answers a dispatch the source cannot cover with an insufficient-stock 409", async () => {
    const { m, fixtures } = await movements();
    const requested = (await m.listTransfers({ status: "REQUESTED" })).items[0];
    if (!requested) throw new Error("no requested fixture transfer");
    await m.approveTransfer(requested.id);
    const line = (await m.getTransfer(requested.id)).lines[0];
    const index = fixtures.INVENTORY.findIndex((i) => i.id === line?.inventory_item_id);
    const stocked = fixtures.INVENTORY[index];
    if (!stocked) throw new Error("no fixture item");
    fixtures.INVENTORY[index] = { ...stocked, available_qty: "5" };

    expect(await refusal(() => m.dispatchTransfer(requested.id, {}, "k"))).toMatchObject({
      status: 409,
      problem: { type: "https://errors.opsgrids.dev/insufficient-stock", errors: [{ available: "5" }] },
    });
  });

  it("receives a transfer in parts, writes off a shortage, and refuses more than is in transit", async () => {
    const { m } = await movements();
    const moving = (await m.listTransfers({ status: "IN_TRANSIT" })).items[0];
    if (!moving) throw new Error("no in-transit fixture transfer");
    const line = (await m.getTransfer(moving.id)).lines[0];
    if (!line) throw new Error("no line");
    expect(line).toMatchObject({ dispatched: "40", received: "10", in_transit: "30" });

    const tooMuch = await refusal(() =>
      m.receiveTransfer(moving.id, { lines: [{ line_id: line.id, received_quantity: "30", shortage_quantity: "0.001" }] }, "k"),
    );
    expect(tooMuch).toMatchObject({ status: 409 });

    const partial = await m.receiveTransfer(
      moving.id,
      { lines: [{ line_id: line.id, received_quantity: "20.5", shortage_quantity: "0" }] },
      "k",
    );
    expect(partial).toMatchObject({ status: "IN_TRANSIT" });
    expect(partial.lines[0]).toMatchObject({ received: "30.5", in_transit: "9.5" });

    const done = await m.receiveTransfer(
      moving.id,
      { lines: [{ line_id: line.id, received_quantity: "7", shortage_quantity: "2.5" }] },
      "k",
    );
    expect(done).toMatchObject({ status: "RECEIVED" });
    expect(done.lines[0]).toMatchObject({ received: "37.5", shortage: "2.5", in_transit: "0" });
  });

  it("reports in-transit stock beside the physical figure, and pages by cursor", async () => {
    const { m } = await movements();
    const rows = await m.listInTransit();
    expect(rows[0]).toMatchObject({ in_transit: "30" });
    expect(typeof rows[0]?.physical).toBe("string");

    const first = await m.listTransfers({ limit: 2 });
    expect(first.items).toHaveLength(2);
    expect(first.next_cursor).not.toBeNull();
    const second = await m.listTransfers({ limit: 2, ...(first.next_cursor ? { cursor: first.next_cursor } : {}) });
    expect(second.items).toHaveLength(1);
    expect(second.next_cursor).toBeNull();
  });

  it("refuses a threshold of zero or junk on its own field, and stores a valid one", async () => {
    const { m } = await movements();
    expect(
      await refusal(() => m.putControlSettings({ transfer_approval_threshold: "0", adjustment_approval_threshold: null })),
    ).toMatchObject({ status: 422, problem: { errors: [{ field: "transfer_approval_threshold" }] } });
    expect(
      await refusal(() => m.putControlSettings({ transfer_approval_threshold: null, adjustment_approval_threshold: "abc" })),
    ).toMatchObject({ status: 422, problem: { errors: [{ field: "adjustment_approval_threshold" }] } });
    expect(await m.putControlSettings({ transfer_approval_threshold: "250.5", adjustment_approval_threshold: null })).toMatchObject({
      transfer_approval_threshold: "250.5",
      adjustment_approval_threshold: null,
    });
    expect(await m.getControlSettings()).toMatchObject({ transfer_approval_threshold: "250.5" });
  });
});

describe("the order payment card in fixture mode", () => {
  it("answers an order's payments from sample data instead of calling a server that is not there", async () => {
    const { client, fixtures } = await fixtureMode();
    const order = fixtures.ORDERS.find((o) => o.status !== "DRAFT");
    if (order === undefined) throw new Error("no fixture order");
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const payments = await client.getOrderPayments(order.id);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(payments).toMatchObject({
      order_id: order.id,
      total_minor: order.total_minor,
      currency: order.currency,
      paid_at: order.paid_at ?? null,
      can_collect: false,
      online_payments_configured: false,
      collections: [],
    });
    expect(payments.collect_blocked_reason).toMatch(/sample data/i);
  });

  it("refuses an order that does not exist with the not-found answer the order page gives", async () => {
    const { client } = await fixtureMode();
    await expect(client.getOrderPayments("70570000-0000-4000-8000-0000000fffff")).rejects.toThrow(/could not be found/);
  });
});
