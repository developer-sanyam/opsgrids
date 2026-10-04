import { useMemo } from "react";

import { listLocations } from "@/api/client";
import { useAsync } from "@opsgrids/client";
import type { SelectOption } from "@/ui";

/**
 * A warehouse's locations as `Select` options (`code — name`), for the one warehouse a dialog is
 * working in. An empty `warehouseId` loads nothing and reports `idle`, so a dialog can render its
 * location control before the warehouse is chosen.
 */
export function useLocationOptions(warehouseId: string, exclude?: string) {
  const loader = () => (warehouseId === "" ? Promise.resolve([]) : listLocations(warehouseId));
  const state = useAsync(loader, [warehouseId]);
  const options: SelectOption[] = useMemo(() => {
    if (state.status !== "success") return [];
    return state.data
      .filter((l) => l.id !== exclude)
      .map((l) => ({ value: l.id, label: l.name === l.code ? l.code : `${l.code} — ${l.name}` }));
  }, [state, exclude]);
  return {
    options,
    status: warehouseId === "" ? ("idle" as const) : state.status,
    reload: state.reload,
  };
}
