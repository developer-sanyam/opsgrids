import { useMemo } from "react";

import { FULL_PAGE, listLocations, listWarehouses } from "@/api/client";
import { useAsync } from "@opsgrids/client";
import type { SelectOption } from "@/ui";

/**
 * The locations a delivery into `warehouseName` can be put away to, as `Select` options. A
 * purchase order names its warehouse by name, locations hang off a warehouse id, so this resolves
 * the one to the other. When the locations cannot be loaded the form still works: every line
 * simply goes to its item's default location.
 */
export function useReceivingLocations(warehouseName: string) {
  const state = useAsync(async () => {
    const warehouses = await listWarehouses({ size: FULL_PAGE });
    const warehouse = warehouses.items.find((w) => w.name === warehouseName);
    return warehouse ? listLocations(warehouse.id) : [];
  }, [warehouseName]);

  const options: SelectOption[] = useMemo(() => {
    if (state.status !== "success") return [];
    return state.data
      .slice()
      .sort((a, b) => a.code.localeCompare(b.code))
      .map((l) => ({ value: l.id, label: `${l.code} · ${l.name}` }));
  }, [state]);

  return { options, status: state.status };
}
