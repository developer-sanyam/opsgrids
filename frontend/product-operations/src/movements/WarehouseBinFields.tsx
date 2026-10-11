import { useLocationChoices } from "./useMovementReference";
import { Select } from "@/ui";
import type { SelectOption } from "@/ui";

interface WarehouseBinFieldsProps {
  warehouseOptions: SelectOption[];
  warehousesLoading: boolean;
  warehouseId: string;
  onWarehouseChange: (warehouseId: string) => void;
  locationId: string;
  onLocationChange: (locationId: string) => void;
  locationError?: string | undefined;
  /** "Bin" by default; a transfer line says "Take from bin". */
  locationLabel?: string;
  /** Names the pair when a form has more than one, so the accessible names stay distinct. */
  warehouseLabel?: string;
}

/**
 * Where stock sits is a bin inside a warehouse, so choosing one is two steps: the warehouse, then
 * one of its locations. The location list is fetched for the chosen warehouse and stays disabled —
 * with a hint saying why — until there is one.
 */
export function WarehouseBinFields({
  warehouseOptions,
  warehousesLoading,
  warehouseId,
  onWarehouseChange,
  locationId,
  onLocationChange,
  locationError,
  locationLabel = "Bin",
  warehouseLabel = "Warehouse",
}: WarehouseBinFieldsProps) {
  const choices = useLocationChoices(warehouseId);
  const noLocations = warehouseId !== "" && choices.status === "success" && choices.options.length === 0;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <Select
        label={warehouseLabel}
        value={warehouseId}
        onChange={(e) => {
          onWarehouseChange(e.target.value);
          onLocationChange("");
        }}
        options={warehouseOptions}
        placeholder={warehousesLoading ? "Loading warehouses…" : "Choose a warehouse"}
        disabled={warehousesLoading}
        required
      />
      <Select
        label={locationLabel}
        value={locationId}
        onChange={(e) => onLocationChange(e.target.value)}
        options={choices.options}
        placeholder={
          warehouseId === ""
            ? "Choose a warehouse first"
            : choices.status === "loading"
              ? "Loading locations…"
              : choices.status === "error"
                ? "Couldn't load locations"
                : "Choose a location"
        }
        disabled={warehouseId === "" || choices.status !== "success"}
        required
        {...(locationError !== undefined
          ? { error: locationError }
          : noLocations
            ? { helper: "This warehouse has no locations yet." }
            : {})}
      />
    </div>
  );
}
