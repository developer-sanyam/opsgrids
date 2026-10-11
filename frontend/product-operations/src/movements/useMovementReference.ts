import { useMemo } from "react";

import { FULL_PAGE, listLocations, listUoms } from "@/api/client";
import type { LocationOut } from "@/api/types";
import { useAsync } from "@opsgrids/client";
import { discreteUomCodes } from "@/lib/movementQty";
import type { SelectOption } from "@/ui";

const NONE: ReadonlySet<string> = new Set();

/**
 * The unit codes counted in whole units only, from the org's UOM directory. While the directory
 * loads (or if it cannot) the set is empty, so a fraction is not refused on a guess: the server
 * validates a quantity against the item's unit regardless.
 */
export function useDiscreteUoms(): ReadonlySet<string> {
  const state = useAsync(() => listUoms({ size: FULL_PAGE }), []);
  return useMemo(() => (state.status === "success" ? discreteUomCodes(state.data.items) : NONE), [state]);
}

export interface LocationChoices {
  status: "loading" | "error" | "success";
  options: SelectOption[];
  /** The warehouse's default bin — where received stock lands unless told otherwise. */
  defaultId: string;
  locations: LocationOut[];
}

/** A warehouse's locations as `Select` options, "CODE · Name". Empty until a warehouse is chosen. */
export function useLocationChoices(warehouseId: string): LocationChoices {
  const state = useAsync(
    () => (warehouseId === "" ? Promise.resolve<LocationOut[]>([]) : listLocations(warehouseId)),
    [warehouseId],
  );
  return useMemo(() => {
    if (state.status !== "success") {
      return { status: state.status, options: [], defaultId: "", locations: [] };
    }
    return {
      status: "success",
      options: state.data.map((l) => ({ value: l.id, label: `${l.code} · ${l.name}` })),
      defaultId: state.data.find((l) => l.is_default)?.id ?? "",
      locations: state.data,
    };
  }, [state]);
}
