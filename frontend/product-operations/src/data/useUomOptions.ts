import { useMemo } from "react";

import { listUoms } from "@/api/client";
import { useAsync } from "@opsgrids/client";
import type { SelectOption } from "@/ui";

/**
 * The org's UOM directory, as `Select` options — fed by `GET /operations/inventory/uoms`.
 * Registry units first (seeded lazily on first read), then any custom unit an item has used.
 */
export function useUomOptions() {
  const loader = () => listUoms({ size: 200 });
  const state = useAsync(loader, []);
  const options: SelectOption[] = useMemo(() => {
    if (state.status !== "success") return [];
    return state.data.items.map((u) => ({ value: u.id, label: `${u.name} (${u.code})` }));
  }, [state]);
  return { options, status: state.status, reload: state.reload };
}
