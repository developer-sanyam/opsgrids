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
});
