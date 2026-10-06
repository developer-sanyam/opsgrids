import { useMemo } from "react";
import { InfoWindow, Map, Marker } from "@vis.gl/react-google-maps";

import type { WarehouseMapSiteOut } from "@/api/types";
import { microToDegrees } from "@/lib/geo";
import { formatNumber } from "@/lib/format";
import { Button } from "@/ui";
import { MapLoadingOverlay } from "./MapLoadingOverlay";
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

/** A zoom that frames one warehouse's neighbourhood, for a map with a single pin or pins that sit
 *  on top of each other. */
const SINGLE_SITE_ZOOM = 13;
const FRAME_PADDING_PX = 64;
/** Sites closer than this on both axes (about a kilometre) are framed as one place. A bounds fit
 *  around them would zoom in to street level, and around identical coordinates, which have no extent
 *  at all, to a rooftop. */
const NEARBY_SPAN_MICRO = 10_000;

/**
 * The warehouses on a Google map, one pin each, with a popup of what the Operations team asks of a
 * site: its address, how many floors, how many bins hold stock, and the way into its floors.
 *
 * Google draws the popup's bubble white whatever the app's theme, so the popup's text is never left
 * to sit on it: the heading and the body each paint their own `surface`, the background the `ink` and
 * `muted` tokens are contrast-checked against in both themes. (Theme-token text straight on the
 * white bubble is near-invisible in dark.)
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
    const lats = pins.map((p) => p.site.lat_micro);
    const lngs = pins.map((p) => p.site.lng_micro);
    const [north, south, east, west] = [Math.max(...lats), Math.min(...lats), Math.max(...lngs), Math.min(...lngs)];
    if (north - south <= NEARBY_SPAN_MICRO && east - west <= NEARBY_SPAN_MICRO) {
      return {
        defaultCenter: { lat: microToDegrees((north + south) / 2), lng: microToDegrees((east + west) / 2) },
        defaultZoom: SINGLE_SITE_ZOOM,
      };
    }
    return {
      defaultBounds: {
        north: microToDegrees(north),
        south: microToDegrees(south),
        east: microToDegrees(east),
        west: microToDegrees(west),
        padding: FRAME_PADDING_PX,
      },
    };
  }, [pins]);

  const selected = pins.find((p) => p.site.id === selectedId) ?? null;

  return (
    <MapShell apiKey={apiKey} onFailure={onFailure}>
      <div className="relative h-96 w-full overflow-hidden rounded-tile" data-testid="sites-map">
        <MapLoadingOverlay />
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
                headerContent={<span className="rounded-chip bg-surface px-2 py-1 font-semibold text-ink">{selected.site.name}</span>}
                pixelOffset={[0, -36]}
                onCloseClick={() => onSelect(null)}
              >
                <div className="flex max-w-60 flex-col gap-2 rounded-tile bg-surface p-2 text-sm text-ink">
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
