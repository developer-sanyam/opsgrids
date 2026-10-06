import { useMemo } from "react";

import { listLots, listRolls } from "@/api/tracking";
import { useAsync } from "@opsgrids/client";
import type { SelectOption } from "@/ui";

const PAGE = 200;

/** The lots of one item as `Select` options (newest first). Loads nothing for an empty `itemId`. */
export function useLotOptions(itemId: string) {
  const loader = () => (itemId === "" ? Promise.resolve(null) : listLots({ item_id: itemId, limit: PAGE }));
  const state = useAsync(loader, [itemId]);
  const options: SelectOption[] = useMemo(() => {
    if (state.status !== "success" || state.data === null) return [];
    return state.data.items.map((lot) => ({ value: lot.id, label: lot.lot_no }));
  }, [state]);
  return { options, status: itemId === "" ? ("idle" as const) : state.status, reload: state.reload };
}

/** The active rolls of one item (optionally within one lot) as `Select` options. A split or merged
 *  roll cannot receive stock, so it is not offered. */
export function useRollOptions(itemId: string, lotId: string) {
  const loader = () =>
    itemId === ""
      ? Promise.resolve(null)
      : listRolls({ item_id: itemId, state: "ACTIVE", limit: PAGE, ...(lotId ? { lot_id: lotId } : {}) });
  const state = useAsync(loader, [itemId, lotId]);
  const options: SelectOption[] = useMemo(() => {
    if (state.status !== "success" || state.data === null) return [];
    return state.data.items.map((roll) => ({ value: roll.id, label: roll.roll_no }));
  }, [state]);
  return { options, status: itemId === "" ? ("idle" as const) : state.status, reload: state.reload };
}
