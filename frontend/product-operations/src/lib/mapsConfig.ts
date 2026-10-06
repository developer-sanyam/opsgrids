/**
 * Whether the Google Maps features exist in this build at all.
 *
 * The key is baked into the bundle at build time (`VITE_GOOGLE_MAPS_API_KEY`, set by
 * `infrastructure/edge/publish-web.sh` for the operations app only). A build without it is a
 * complete, supported build: every map surface renders its non-map equivalent and nothing imports
 * the Maps loader. Because the key ships in the bundle it must be referrer- and API-restricted in
 * the Google console — and it is never logged, echoed in an error or put in a report from here.
 */
export function getMapsApiKey(): string | null {
  const raw: unknown = import.meta.env.VITE_GOOGLE_MAPS_API_KEY;
  if (typeof raw !== "string") return null;
  const key = raw.trim();
  return key === "" ? null : key;
}

export function mapsEnabled(): boolean {
  return getMapsApiKey() !== null;
}
