import type { LocationKind } from "@/api/types";
import type { SelectOption } from "@/ui";

/**
 * The full `Location.kind` vocabulary (Fulfilment plan F1, ADR-0024 §5), as the one label map
 * every screen that shows or edits a location's kind reads — so "pack_station" reads
 * "Pack station" everywhere rather than once per screen's own guess at the copy.
 */
export const LOCATION_KIND_LABEL: Record<LocationKind, string> = {
  zone: "Zone",
  aisle: "Aisle",
  rack: "Rack",
  shelf: "Shelf",
  bin: "Bin",
  staging: "Staging",
  pack_station: "Pack station",
  dock: "Dock",
  receiving: "Receiving",
  returns: "Returns",
  quarantine: "Quarantine",
};

export const LOCATION_KIND_OPTIONS: SelectOption[] = (
  Object.keys(LOCATION_KIND_LABEL) as LocationKind[]
).map((kind) => ({ value: kind, label: LOCATION_KIND_LABEL[kind] }));
