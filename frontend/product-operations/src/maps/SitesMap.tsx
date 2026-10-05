import { useMemo } from "react";
import { InfoWindow, Map, Marker } from "@vis.gl/react-google-maps";

import type { WarehouseMapSiteOut } from "@/api/types";
import { microToDegrees } from "@/lib/geo";
import { formatNumber } from "@/lib/format";
import { Button } from "@/ui";
import { MapShell } from "./MapShell";
import type { ReportReason } from "@/lib/clientReport";

interface SitesMapProps {
  apiKey: string;
  /** Only sites whose coordinates are a valid pair — the page filters, so nothing is drawn at (0, 0). */
  sites: WarehouseMapSiteOut[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onOpenFloors: (warehouseId: string) => void;
  onFailure: (reason: Extract<ReportReason, "script_error" | "auth_failure">) => void;
}

interface Pin {
  site: WarehouseMapSiteOut;
  position: { lat: number; lng: number };
}

/** A zoom that frames one warehouse's neighbourhood, for a map with a single pin. */
const SINGLE_SITE_ZOOM = 13;
const FRAME_PADDING_PX = 64;

/**
 * The warehouses on a Google map, one pin each, with a popup of what the Operations team asks of a
 * site: its address, how many floors, how many bins hold stock, and the way into its floors.
 *
 * The map itself is not a keyboard surface — the list beside it is the accessible way to the same
 * information and the same actions (`WarehousesMap.tsx`).
 *
 * Pins use the classic `Marker`, which needs no Cloud Map ID; `AdvancedMarker` is a swap once the
 * owner has created one.
 */
export default function SitesMap({ apiKey, sites, selectedId, onSelect, onOpenFloors, onFailure }: SitesMapProps) {
  const pins = useMemo<Pin[]>(
    () =>
      sites.map((site) => ({
        site,
        position: { lat: microToDegrees(site.lat_micro), lng: microToDegrees(site.lng_micro) },
      })),
    [sites],
  );

  const frame = useMemo(() => {
    if (pins.length === 0) return null;
    if (pins.length === 1) return { defaultCenter: pins[0]?.position ?? { lat: 0, lng: 0 }, defaultZoom: SINGLE_SITE_ZOOM };
    const lats = pins.map((p) => p.position.lat);
    const lngs = pins.map((p) => p.position.lng);
    return {
      defaultBounds: {
        north: Math.max(...lats),
        south: Math.min(...lats),
        east: Math.max(...lngs),
        west: Math.min(...lngs),
        padding: FRAME_PADDING_PX,
      },
    };
  }, [pins]);

  const selected = pins.find((p) => p.site.id === selectedId) ?? null;

  return (
    <MapShell apiKey={apiKey} onFailure={onFailure}>
      <div className="h-96 w-full overflow-hidden rounded-tile" data-testid="sites-map">
        {frame ? (
          <Map
            {...frame}
            gestureHandling="cooperative"
            mapTypeControl={false}
            streetViewControl={false}
            fullscreenControl={false}
            onClick={() => onSelect(null)}
          >
            {pins.map(({ site, position }) => (
              <Marker
                key={site.id}
                position={position}
                title={site.name}
                onClick={() => onSelect(site.id)}
              />
            ))}
            {selected ? (
              <InfoWindow
                position={selected.position}
                headerContent={<span className="font-semibold text-ink">{selected.site.name}</span>}
                pixelOffset={[0, -36]}
                onCloseClick={() => onSelect(null)}
              >
                <div className="flex max-w-60 flex-col gap-2 text-sm text-ink">
                  <p>{selected.site.location || "No address recorded"}</p>
                  <p className="tnum text-muted">
                    {formatNumber(selected.site.floor_count)} floor{selected.site.floor_count === 1 ? "" : "s"} ·{" "}
                    {formatNumber(selected.site.stocked_location_count)} stocked bin
                    {selected.site.stocked_location_count === 1 ? "" : "s"}
                  </p>
                  <Button size="sm" onClick={() => onOpenFloors(selected.site.id)}>
                    Open floors
                  </Button>
                </div>
              </InfoWindow>
            ) : null}
          </Map>
        ) : null}
      </div>
    </MapShell>
  );
}
