import { useMemo } from "react";

import { FULL_PAGE, listWarehouses } from "@/api/client";
import { useAsync } from "@opsgrids/client";
import type { SelectOption } from "@/ui";

/**
 * The warehouse directory, as `Select` options — fed by `GET /operations/warehouses`. Every form
 * that names a warehouse (an inventory item, a purchase order) reads this one hook rather than
 * re-fetching the list itself.
 */
export function useWarehouseOptions() {
  const loader = () => listWarehouses({ size: FULL_PAGE });
  const state = useAsync(loader, []);
  const options: SelectOption[] = useMemo(() => {
    if (state.status !== "success") return [];
    return state.data.items.map((w) => ({ value: w.name, label: w.name }));
  }, [state]);
  return { options, status: state.status, reload: state.reload };
}

/**
 * The same directory, keyed on `id` rather than `name` — every order endpoint (Fulfilment F2)
 * addresses a warehouse by `warehouse_id`, unlike a purchase order's denormalised `warehouse`
 * name, so the orders screens read this one instead of `useWarehouseOptions`.
 */
export function useWarehouseIdOptions() {
  const loader = () => listWarehouses({ size: FULL_PAGE });
  const state = useAsync(loader, []);
  const options: SelectOption[] = useMemo(() => {
    if (state.status !== "success") return [];
    return state.data.items.map((w) => ({ value: w.id, label: w.name }));
  }, [state]);
  return { options, status: state.status, reload: state.reload };
}
