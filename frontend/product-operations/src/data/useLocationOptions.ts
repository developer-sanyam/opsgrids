import { useMemo } from "react";

import { FULL_PAGE, listLocations, listWarehouses } from "@/api/client";
import { useAsync } from "@opsgrids/client";
import type { SelectOption } from "@/ui";

/**
 * Every location in every warehouse, as `Select` options labelled "WH1 · BIN-A1 — Bin A1".
 *
 * Production orders and their material moves name locations across the whole organization, and
 * locations are only listed per warehouse, so this reads the (small) warehouse directory and each
 * warehouse's tree once. `label` resolves an id for display and falls back to a short id when a
 * location has since gone, so a table never shows a blank.
 */
export function useLocationOptions() {
  const state = useAsync(async () => {
    const warehouses = await listWarehouses({ size: FULL_PAGE });
    const trees = await Promise.all(
      warehouses.items.map(async (w) => (await listLocations(w.id)).map((l) => ({ l, w }))),
    );
    return trees.flat();
  }, []);

  const options: SelectOption[] = useMemo(
    () =>
      state.status === "success"
        ? state.data.map(({ l, w }) => ({ value: l.id, label: `${w.code} · ${l.code} — ${l.name}` }))
        : [],
    [state],
  );
  const labels = useMemo(() => new Map(options.map((o) => [o.value, o.label])), [options]);

  return {
    status: state.status,
    error: state.error,
    reload: state.reload,
    options,
    label: (id: string) => labels.get(id) ?? id.slice(0, 8),
  };
}
