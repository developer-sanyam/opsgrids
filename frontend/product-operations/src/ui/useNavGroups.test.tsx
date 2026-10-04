import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";

import { PermissionProvider } from "@opsgrids/client";
import { useNavGroups } from "./useNavGroups";

function wrapper(grants: string[]) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <PermissionProvider grants={grants} entitlements={[]}>
        {children}
      </PermissionProvider>
    );
  };
}

function labels(grants: string[]): string[] {
  const { result } = renderHook(() => useNavGroups(), { wrapper: wrapper(grants) });
  return result.current.groups.flatMap((g) => g.items.map((i) => i.label));
}

describe("useNavGroups", () => {
  it("lists Stock, Lots, Rolls and the Stock Ledger right after Inventory for a caller who can read stock", () => {
    const all = labels(["operations.inventory.stock.read:ORG"]);
    const at = all.indexOf("Inventory");
    expect(all.slice(at, at + 5)).toEqual(["Inventory", "Stock", "Lots", "Rolls", "Stock Ledger"]);
  });

  it("leaves them out for a caller who can't", () => {
    const all = labels([]);
    for (const label of ["Stock", "Lots", "Rolls", "Stock Ledger"]) expect(all).not.toContain(label);
    expect(all).toContain("Inventory");
  });

  it("points each entry at the route registered ahead of inventory/:id", () => {
    const { result } = renderHook(() => useNavGroups(), {
      wrapper: wrapper(["operations.inventory.stock.read:ORG"]),
    });
    const targets = result.current.groups.flatMap((g) => g.items.map((i) => i.to));
    for (const to of ["/inventory/stock", "/inventory/lots", "/inventory/rolls", "/inventory/ledger"]) {
      expect(targets).toContain(to);
    }
  });

  it("puts the stock movement screens after the tracking ones for someone who can read stock", () => {
    const all = labels(["operations.inventory.stock.read:ORG"]);
    const at = all.indexOf("Inventory");
    expect(all.slice(at, at + 8)).toEqual([
      "Inventory",
      "Stock",
      "Lots",
      "Rolls",
      "Stock Ledger",
      "Transfers",
      "Reservations",
      "Adjustments",
    ]);
    expect(all).not.toContain("Approval Thresholds");
  });

  it("leaves the movement screens out for someone who cannot read stock", () => {
    const all = labels([]);
    for (const label of ["Transfers", "Reservations", "Adjustments", "Approval Thresholds"]) {
      expect(all).not.toContain(label);
    }
  });

  it("offers the approval thresholds to someone who can configure them", () => {
    expect(labels(["operations.inventory.control.configure:ORG"])).toContain("Approval Thresholds");
  });

  it("does not take an own-scope read for an organization-wide one", () => {
    expect(labels(["operations.inventory.stock.read:OWN"])).not.toContain("Transfers");
  });
});
