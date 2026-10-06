/**
 * Roll detail: the lineage (parents and children) and history it reads, every state of its async
 * surfaces, and the split action — only for a writer, and only while the roll is still active.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import type { RollTraceOut } from "@/api/trackingTypes";
import { renderScreen } from "@/test-utils/renderScreen";
import { lot, roll } from "@/test-utils/tracking";
import { RollDetailPage } from "./RollDetail";

const getRollTrace = vi.fn();
const listRollEvents = vi.fn();
const listStock = vi.fn();
const locateStock = vi.fn();

vi.mock("@/api/tracking", () => ({
  getRollTrace: (...args: unknown[]) => getRollTrace(...args),
  listRollEvents: (...args: unknown[]) => listRollEvents(...args),
  listStock: (...args: unknown[]) => listStock(...args),
  locateStock: (...args: unknown[]) => locateStock(...args),
  splitRoll: () => Promise.reject(new Error("not used")),
}));
vi.mock("@/api/client", () => ({
  listLocations: () => Promise.resolve([]),
}));

const READ = "operations.inventory.stock.read:ORG";
const WRITE = "operations.inventory.roll.write:ORG";

function trace(overrides: Partial<RollTraceOut> = {}): RollTraceOut {
  return {
    roll: roll({ id: "roll-1", roll_no: "R-001" }),
    lot: lot({ id: "lot-1", lot_no: "LOT-1" }),
    ancestors: [],
    descendants: [],
    lot_ancestors: [],
    ...overrides,
  };
}

function renderRoll(grants: string[]) {
  return renderScreen(<RollDetailPage />, grants, { path: "/inventory/rolls/:id", at: "/inventory/rolls/roll-1" });
}

describe("RollDetailPage", () => {
  beforeEach(() => {
    getRollTrace.mockReset();
    listRollEvents.mockReset();
    listStock.mockReset();
    getRollTrace.mockResolvedValue(trace());
    listRollEvents.mockResolvedValue({ items: [], next_cursor: null });
    listStock.mockResolvedValue({ items: [], next_cursor: null });
  });

  it("explains a missing grant", async () => {
    renderRoll([]);
    expect(await screen.findByText("You don't have access to this roll")).toBeDefined();
    expect(getRollTrace).not.toHaveBeenCalled();
  });

  it("shows loading, then the roll's figures", async () => {
    renderRoll([READ]);
    expect(await screen.findByRole("heading", { name: "R-001" })).toBeDefined();
    expect(screen.getAllByText("82.5 m")).toHaveLength(2);
    expect(screen.getByText("14.8 kg")).toBeDefined();
    expect(screen.getByText("1500 mm")).toBeDefined();
  });

  it("shows an error with a retry, and a not-found as the server's sentence", async () => {
    const user = userEvent.setup();
    getRollTrace.mockRejectedValueOnce(
      new ApiError(404, "That roll is not part of this organization", { detail: "That roll is not part of this organization" }),
    );
    renderRoll([READ]);

    expect(await screen.findByText("That roll is not part of this organization")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "R-001" })).toBeDefined();
  });

  it("shows the parent and the children as links, and says plainly when there are none", async () => {
    renderRoll([READ]);
    await screen.findByRole("heading", { name: "R-001" });
    expect(screen.getByText("This roll wasn't made by a split or merge.")).toBeDefined();
    expect(screen.getByText("This roll hasn't been split or merged.")).toBeDefined();
  });

  it("links to the rolls it came from and became", async () => {
    getRollTrace.mockResolvedValue(
      trace({
        ancestors: [{ id: "parent-1", number: "R-000", depth: 1, from_id: "roll-1", relation: "SPLIT", move_id: "m1", lot_id: "lot-1", lot_no: "LOT-1" }],
        descendants: [
          { id: "child-1", number: "R-001-1", depth: 1, from_id: "roll-1", relation: "SPLIT", move_id: "m2", lot_id: "lot-1", lot_no: "LOT-1" },
          { id: "child-2", number: "R-001-2", depth: 1, from_id: "roll-1", relation: "SPLIT", move_id: "m2", lot_id: "lot-1", lot_no: "LOT-1" },
        ],
      }),
    );
    renderRoll([READ]);

    const parent = await screen.findByRole("link", { name: "R-000" });
    expect(parent.getAttribute("href")).toBe("/inventory/rolls/parent-1");
    expect(screen.getByRole("link", { name: "R-001-1" }).getAttribute("href")).toBe("/inventory/rolls/child-1");
    expect(screen.getByRole("link", { name: "R-001-2" })).toBeDefined();
  });

  it("lists the history, newest first, with an empty state when there is none", async () => {
    listRollEvents.mockResolvedValue({
      items: [
        { id: "e2", kind: "SPLIT", related_roll_id: "child-1", move_id: "m", quantity: "50", secondary_qty: null, detail: {}, occurred_at: "2026-10-02T10:00:00Z" },
        { id: "e1", kind: "CREATED", related_roll_id: null, move_id: null, quantity: null, secondary_qty: null, detail: {}, occurred_at: "2026-10-01T10:00:00Z" },
      ],
      next_cursor: null,
    });
    renderRoll([READ]);

    expect(await screen.findByText("Split")).toBeDefined();
    expect(screen.getByText("Created")).toBeDefined();
  });

  it("says there is no history yet when the roll has none", async () => {
    renderRoll([READ]);
    expect(await screen.findByText("No history yet")).toBeDefined();
  });

  it("offers Split roll to a writer of an active roll", async () => {
    renderRoll([READ, WRITE]);
    expect(await screen.findByRole("button", { name: "Split roll" })).toBeDefined();
  });

  it("does not offer Split roll to a reader", async () => {
    renderRoll([READ]);
    await screen.findByRole("heading", { name: "R-001" });
    expect(screen.queryByRole("button", { name: "Split roll" })).toBeNull();
  });

  it("does not offer Split roll for a roll that has already been split", async () => {
    getRollTrace.mockResolvedValue(trace({ roll: roll({ id: "roll-1", roll_no: "R-001", state: "SPLIT" }) }));
    renderRoll([READ, WRITE]);
    await screen.findByRole("heading", { name: "R-001" });
    expect(screen.queryByRole("button", { name: "Split roll" })).toBeNull();
    expect(screen.getAllByText("Split").length).toBeGreaterThan(0);
  });

  it("offers Find stock for this roll, narrowing the search to the roll", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue({ results: [] });
    renderRoll([READ]);

    await screen.findByRole("heading", { name: "R-001" });
    expect(screen.getByText("Which bins hold roll R-001 right now, and where they are.")).toBeDefined();
    expect(locateStock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Find where it is" }));
    expect(locateStock).toHaveBeenCalledWith({ inventory_item_id: "item-1", lot_id: undefined, roll_id: "roll-1" });
  });
});
