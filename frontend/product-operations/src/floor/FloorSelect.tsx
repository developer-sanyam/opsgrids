import { useMemo } from "react";

import { listWarehouseFloors } from "@/api/client";
import { useAsync, useCan } from "@opsgrids/client";
import { Button, Select } from "@/ui";
import type { SelectOption } from "@/ui";

interface FloorSelectProps {
  warehouseId: string;
  /** The chosen floor's id, or `""` for no floor. */
  value: string;
  onChange: (floorId: string) => void;
  /** An extra first option with the empty value, such as "Auto-arranged (all locations)". */
  noneLabel?: string | undefined;
}

/**
 * The floor picker for a floor view. Its own request with its own failure: if the floor list will
 * not load the picker says so and offers a retry, and the plan beside it — which already has the
 * floor it needs from the address — keeps working. A caller who may not read floors sees no picker.
 */
export function FloorSelect({ warehouseId, value, onChange, noneLabel }: FloorSelectProps) {
  const canRead = useCan("operations.warehouse.floor.read", "ORG");
  const floors = useAsync(() => (canRead ? listWarehouseFloors(warehouseId) : Promise.resolve([])), [warehouseId, canRead]);

  const options = useMemo<SelectOption[]>(() => {
    const rows = floors.status === "success" ? floors.data : [];
    return rows
      .slice()
      .sort((a, b) => a.floor_number - b.floor_number)
      .map((f) => ({ value: f.id, label: f.name ? `Floor ${f.floor_number} — ${f.name}` : `Floor ${f.floor_number}` }));
  }, [floors]);

  if (!canRead) return null;

  if (floors.status === "error") {
    return (
      <div className="flex items-center gap-2" role="alert">
        <span className="text-sm text-muted">Couldn&apos;t load the floor list.</span>
        <Button size="sm" variant="secondary" onClick={floors.reload}>
          Try again
        </Button>
      </div>
    );
  }

  return (
    <Select
      label="Floor"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      options={options}
      disabled={floors.status === "loading"}
      {...(noneLabel !== undefined ? { placeholder: noneLabel } : {})}
      containerClassName="w-full sm:w-64"
    />
  );
}
