import { getPublishedLayout, listWarehouseFloors } from "@/api/client";
import { floorTitle } from "@/lib/floorLayoutEditor";
import { useAsync } from "@opsgrids/client";

/**
 * The locations that another floor of this warehouse already holds, as a map from location id to
 * that floor's title.
 *
 * "Already holds" means the floor's *published* layout: that is what publishing a layout writes
 * onto a location (its floor), and what "find stock" and put-away guidance read. A location that
 * is only in another floor's unpublished draft is not on that floor yet. This floor's own
 * published layout is not another floor: a location a draft took off this floor is free to go
 * back on it.
 *
 * One request for the floors and one per other floor — a warehouse has a handful — all through
 * endpoints the editor's own reads already need the permissions for.
 */
export function useLocationsOnOtherFloors(warehouseId: string, floorId: string) {
  return useAsync(async () => {
    const floors = (await listWarehouseFloors(warehouseId))
      .filter((floor) => floor.id !== floorId)
      .sort((a, b) => a.floor_number - b.floor_number);
    const layouts = await Promise.all(floors.map((floor) => getPublishedLayout(floor.id)));
    const onOtherFloors = new Map<string, string>();
    floors.forEach((floor, index) => {
      for (const placement of layouts[index]?.placements ?? []) {
        if (!onOtherFloors.has(placement.location_id)) onOtherFloors.set(placement.location_id, floorTitle(floor));
      }
    });
    return onOtherFloors;
  }, [warehouseId, floorId]);
}
