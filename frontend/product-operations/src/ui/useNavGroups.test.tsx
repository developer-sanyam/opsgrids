import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { PermissionProvider } from "@opsgrids/client";
import { useNavGroups } from "./useNavGroups";

function wrapper(grants: string[], at = "/") {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <MemoryRouter initialEntries={[at]}>
        <PermissionProvider grants={grants} entitlements={[]}>
          {children}
        </PermissionProvider>
      </MemoryRouter>
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

  it("lists the warehouse map straight after Warehouses, whatever the caller can read", () => {
    for (const grants of [[], ["operations.inventory.stock.read:ORG"]]) {
      const all = labels(grants);
      const at = all.indexOf("Warehouses");
      expect(all.slice(at, at + 2)).toEqual(["Warehouses", "Warehouse Map"]);
    }
  });

  it("points the map entry at the route registered ahead of warehouses/:id", () => {
    const { result } = renderHook(() => useNavGroups(), { wrapper: wrapper([]) });
    const targets = result.current.groups.flatMap((g) => g.items.map((i) => i.to));
    expect(targets).toContain("/warehouses/map");
  });

  it("matches Warehouses exactly on the map page, so one screen never lights two entries, and as a prefix everywhere else", () => {
    const endOf = (at: string) => {
      const { result } = renderHook(() => useNavGroups(), { wrapper: wrapper([], at) });
      return result.current.groups.flatMap((g) => g.items).find((i) => i.to === "/warehouses")?.end;
    };
    expect(endOf("/warehouses/map")).toBe(true);
    expect(endOf("/warehouses")).toBe(false);
    expect(endOf("/warehouses/wh-1/floors")).toBe(false);
    expect(endOf("/warehouses/wh-1/floors/f-1/view")).toBe(false);
  });
});
