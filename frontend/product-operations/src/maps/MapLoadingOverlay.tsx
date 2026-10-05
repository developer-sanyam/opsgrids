import { useApiLoadingStatus } from "@vis.gl/react-google-maps";

import { useSlowLoad } from "@/lib/useSlowLoad";
import { SlowLoadNotice } from "@/ui/SlowLoadNotice";
import { MapPinIcon } from "@opsgrids/ui";
import { isScriptPending } from "./scriptStatus";

/**
 * What a map box shows until Google's script has loaded. The `Map` component draws nothing at all
 * until then, so without this the box is blank for as long as the script takes — and the page's own
 * skeleton (`Suspense`) covers only the small local chunk, which is done long before.
 *
 * Fills the box it is placed in (the parent is `relative` and keeps its height, so nothing shifts
 * when the map arrives). After `SLOW_LOAD_MS` it says it is taking longer; `MapShell` gives up on
 * the map altogether at `SCRIPT_HANG_MS`, and the page's list takes over. Render it inside a
 * `MapShell`, beside the `Map`.
 */
export function MapLoadingOverlay() {
  const pending = isScriptPending(useApiLoadingStatus());
  const slow = useSlowLoad(pending);
  if (!pending) return null;
  return (
    <div
      className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-sunken px-4 text-center"
      aria-busy="true"
      data-testid="map-loading"
    >
      <MapPinIcon className="size-6 text-muted" aria-hidden />
      <p className="text-sm text-muted">Loading the map…</p>
      <SlowLoadNotice show={slow} what="the map" />
    </div>
  );
}
