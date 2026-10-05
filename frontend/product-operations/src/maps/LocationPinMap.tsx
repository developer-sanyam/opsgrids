import { Circle, Map, Marker } from "@vis.gl/react-google-maps";

import type { ReportReason } from "@/lib/clientReport";
import { MapShell } from "./MapShell";

interface LocationPinMapProps {
  apiKey: string;
  lat: number;
  lng: number;
  /** The fix's reported accuracy in metres; drawn as a circle around the pin when it is positive. */
  accuracyM: number | null;
  onFailure: (reason: Extract<ReportReason, "script_error" | "auth_failure">) => void;
}

/** Street level for a good fix; a coarse one (a phone on cell towers) needs the surroundings. */
function zoomFor(accuracyM: number | null): number {
  if (accuracyM === null || accuracyM <= 50) return 18;
  if (accuracyM <= 500) return 16;
  return 14;
}

/**
 * Where a worker was when they stored the stock: a pin, and a circle the size of the fix's error so
 * the position is never read as more exact than it is. Rendered only inside the audited reveal
 * panel; the coordinates arrive as props from that panel's state and go nowhere else.
 */
export default function LocationPinMap({ apiKey, lat, lng, accuracyM, onFailure }: LocationPinMapProps) {
  const center = { lat, lng };
  return (
    <MapShell apiKey={apiKey} onFailure={onFailure}>
      <div className="h-64 w-full overflow-hidden rounded-tile" data-testid="location-pin-map">
        <Map
          defaultCenter={center}
          defaultZoom={zoomFor(accuracyM)}
          gestureHandling="cooperative"
          mapTypeControl={false}
          streetViewControl={false}
          fullscreenControl={false}
        >
          <Marker position={center} title="Where the stock was put away" />
          {accuracyM !== null && accuracyM > 0 ? (
            <Circle
              center={center}
              radius={accuracyM}
              strokeWeight={1}
              fillOpacity={0.15}
              clickable={false}
            />
          ) : null}
        </Map>
      </div>
    </MapShell>
  );
}
