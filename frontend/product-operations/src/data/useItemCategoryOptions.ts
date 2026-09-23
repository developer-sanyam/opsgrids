import { useMemo } from "react";

import { listItemCategories } from "@/api/client";
import { useAsync } from "@opsgrids/client";
import type { SelectOption } from "@/ui";

/**
 * The item master's own category tree, as flat `Select` options — fed by
 * `GET /operations/inventory/categories`. Additive to (never a replacement for) the fixed
 * procurement `Category` enum every item already carries.
 */
export function useItemCategoryOptions() {
  const loader = () => listItemCategories({ size: 200 });
  const state = useAsync(loader, []);
  const options: SelectOption[] = useMemo(() => {
    if (state.status !== "success") return [];
    return state.data.items.map((c) => ({ value: c.id, label: c.name }));
  }, [state]);
  return { options, status: state.status, reload: state.reload, items: state.status === "success" ? state.data.items : [] };
}
