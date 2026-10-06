/**
 * The one location fix a storage put-away asks the phone for (spec §6).
 *
 * The fix is audit evidence, never the location of record — the scanned bin is — so nothing here
 * may stand between a worker and a recorded put-away. One request, high accuracy, a hard five
 * seconds; denied, timed out, unsupported, offline or unusable all resolve to `skipped` with a
 * reason the screen turns into a non-blocking sentence. Nothing in this file throws, retries, logs
 * or keeps a position: coordinates live in the request body, and in the queue entry until it
 * syncs, and nowhere else.
 */

export const GEO_TIMEOUT_MS = 5000;
/** The browser's own `timeout` does not start counting while a permission prompt is on screen, so
 *  a worker who ignores the prompt would otherwise hold the put-away up indefinitely. */
const GEO_GUARD_MS = GEO_TIMEOUT_MS + 1000;

const MICRO = 1e6;
const MAX_LAT_MICRO = 90 * MICRO;
const MAX_LNG_MICRO = 180 * MICRO;

export type GeoSkipReason = "unsupported" | "denied" | "timeout" | "unavailable" | "offline" | "invalid";

/** What goes on the wire: integers only. `accuracy_m` is left out when the phone did not say. */
export interface GeoFix {
  lat_micro: number;
  lng_micro: number;
  accuracy_m?: number;
}

export type GeoResult = { kind: "fix"; fix: GeoFix } | { kind: "skipped"; reason: GeoSkipReason };

/** Degrees to integer micro-degrees (about 0.1 m). `null` for anything that is not a number. */
export function degreesToMicro(degrees: number): number | null {
  if (!Number.isFinite(degrees)) return null;
  // `|| 0` folds a negative zero (`Math.round(-0.3)`) into plain 0 so it serialises as "0".
  return Math.round(degrees * MICRO) || 0;
}

/**
 * A browser position to the wire shape — or `null` when it cannot be trusted. Latitude and
 * longitude travel as a pair or not at all, and a pair outside the globe is dropped here rather
 * than left for the server to refuse. A bad accuracy only costs the accuracy.
 */
export function toGeoFix(coords: { latitude: number; longitude: number; accuracy: number }): GeoFix | null {
  const lat = degreesToMicro(coords.latitude);
  const lng = degreesToMicro(coords.longitude);
  if (lat === null || lng === null) return null;
  if (Math.abs(lat) > MAX_LAT_MICRO || Math.abs(lng) > MAX_LNG_MICRO) return null;
  const fix: GeoFix = { lat_micro: lat, lng_micro: lng };
  if (Number.isFinite(coords.accuracy) && coords.accuracy >= 0) fix.accuracy_m = Math.round(coords.accuracy);
  return fix;
}

function skipReasonFor(error: { code?: number }): GeoSkipReason {
  // GeolocationPositionError: 1 PERMISSION_DENIED, 2 POSITION_UNAVAILABLE, 3 TIMEOUT.
  if (error.code === 1) return "denied";
  if (error.code === 3) return "timeout";
  return "unavailable";
}

/**
 * Ask for one fix. Resolves, always, within about six seconds. `online: false` skips the request
 * outright — an offline put-away is queued without a position rather than made to wait for one.
 */
export function requestGeoFix(options: { online: boolean }): Promise<GeoResult> {
  if (!options.online) return Promise.resolve({ kind: "skipped", reason: "offline" });
  if (typeof navigator === "undefined" || !navigator.geolocation) {
    return Promise.resolve({ kind: "skipped", reason: "unsupported" });
  }
  const geolocation = navigator.geolocation;

  return new Promise<GeoResult>((resolve) => {
    let settled = false;
    const finish = (result: GeoResult) => {
      if (settled) return;
      settled = true;
      clearTimeout(guard);
      resolve(result);
    };
    const guard = setTimeout(() => finish({ kind: "skipped", reason: "timeout" }), GEO_GUARD_MS);

    try {
      geolocation.getCurrentPosition(
        (position) => {
          const fix = toGeoFix(position.coords);
          finish(fix ? { kind: "fix", fix } : { kind: "skipped", reason: "invalid" });
        },
        (error) => finish({ kind: "skipped", reason: skipReasonFor(error) }),
        { enableHighAccuracy: true, timeout: GEO_TIMEOUT_MS, maximumAge: 0 },
      );
    } catch {
      // A locked-down WebView can throw synchronously instead of calling the error callback.
      finish({ kind: "skipped", reason: "unsupported" });
    }
  });
}

/** The non-blocking sentence for a put-away that went through without a position. */
export function geoSkipNotice(reason: GeoSkipReason): string {
  switch (reason) {
    case "unsupported":
      return "This phone can't share its location, so the put-away was recorded without it.";
    case "denied":
      return "Location is turned off for this app, so the put-away was recorded without it.";
    case "timeout":
      return "Couldn't get a location fix in time, so the put-away was recorded without it.";
    case "unavailable":
      return "Your location wasn't available just now, so the put-away was recorded without it.";
    case "offline":
      return "You're offline, so the put-away was saved without your location.";
    case "invalid":
      return "The location reading wasn't usable, so the put-away was recorded without it.";
  }
}
