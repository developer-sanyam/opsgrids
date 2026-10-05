/**
 * Coordinates on the wire are **integer micro-degrees** (degrees x 10^6), the same no-float rule the
 * rest of the platform holds for money and quantity: a float latitude is compared, rounded and
 * serialised differently by every runtime, an integer is not. This file is the only place degrees
 * become micro-degrees and back — the warehouse form types degrees, the Maps API speaks degrees, the
 * API stores micro-degrees.
 *
 * Nothing here logs. A worker's coordinates pass through `formatCoordinates` on their way to the
 * one reveal panel and nowhere else.
 */

export const MICRO = 1_000_000;
export const LAT_LIMIT_MICRO = 90 * MICRO;
export const LNG_LIMIT_MICRO = 180 * MICRO;

export type Axis = "lat" | "lng";

const LIMIT: Record<Axis, number> = { lat: LAT_LIMIT_MICRO, lng: LNG_LIMIT_MICRO };
const NAME: Record<Axis, string> = { lat: "Latitude", lng: "Longitude" };
const DEGREES_LIMIT: Record<Axis, number> = { lat: 90, lng: 180 };

/** Degrees to the nearest micro-degree (about 0.11 m at the equator). */
export function degreesToMicro(degrees: number): number {
  const micro = Math.round(degrees * MICRO);
  // `Math.round(-0.4)` is `-0`; an integer on the wire should not carry a sign on zero.
  return micro === 0 ? 0 : micro;
}

export function microToDegrees(micro: number): number {
  return micro / MICRO;
}

/** A field's text for a stored micro-degree value: six decimals, which is every digit it has. */
export function microToFieldText(micro: number | null | undefined): string {
  if (micro === null || micro === undefined || !Number.isInteger(micro)) return "";
  return microToDegrees(micro).toFixed(6);
}

/** Whether `value` is an in-range integer micro-degree for `axis` (a server value, so never assumed). */
export function isValidMicro(value: unknown, axis: Axis): value is number {
  return typeof value === "number" && Number.isInteger(value) && Math.abs(value) <= LIMIT[axis];
}

/** Whether a pair is a usable position: both valid integers in range. */
export function isValidMicroPair(lat: unknown, lng: unknown): boolean {
  return isValidMicro(lat, "lat") && isValidMicro(lng, "lng");
}

/** A stored position as a checked pair, or `null` when either half is absent or out of range. */
export function positionOf(source: {
  lat_micro?: number | null;
  lng_micro?: number | null;
}): { lat_micro: number; lng_micro: number } | null {
  const { lat_micro, lng_micro } = source;
  return isValidMicro(lat_micro, "lat") && isValidMicro(lng_micro, "lng") ? { lat_micro, lng_micro } : null;
}

const DECIMAL = /^[+-]?(?:\d+\.?\d*|\.\d+)$/;

export type ParsedCoordinate =
  | { kind: "empty" }
  | { kind: "value"; micro: number }
  | { kind: "error"; message: string };

/** One coordinate field's text: blank, a valid value, or the sentence to put on the field. */
export function parseCoordinate(text: string, axis: Axis): ParsedCoordinate {
  const trimmed = text.trim();
  if (trimmed === "") return { kind: "empty" };
  if (!DECIMAL.test(trimmed)) {
    return {
      kind: "error",
      message: `${NAME[axis]} is a number in degrees, such as ${axis === "lat" ? "19.076090" : "72.877656"}.`,
    };
  }
  const micro = degreesToMicro(Number(trimmed));
  if (!isValidMicro(micro, axis)) {
    return {
      kind: "error",
      message: `${NAME[axis]} must be between -${DEGREES_LIMIT[axis]} and ${DEGREES_LIMIT[axis]}.`,
    };
  }
  return { kind: "value", micro };
}

export type ParsedPosition =
  | { kind: "none" }
  | { kind: "position"; lat_micro: number; lng_micro: number }
  | { kind: "invalid"; lat?: string; lng?: string };

/**
 * The two coordinate fields together. Both blank is "no position"; one blank is refused on the
 * field that is missing, because the server stores them as a pair and would answer 422.
 */
export function parsePosition(latText: string, lngText: string): ParsedPosition {
  const lat = parseCoordinate(latText, "lat");
  const lng = parseCoordinate(lngText, "lng");
  if (lat.kind === "empty" && lng.kind === "empty") return { kind: "none" };
  const errors: { lat?: string; lng?: string } = {};
  if (lat.kind === "error") errors.lat = lat.message;
  if (lng.kind === "error") errors.lng = lng.message;
  if (lat.kind === "empty") errors.lat = "Enter the latitude too, or clear both fields.";
  if (lng.kind === "empty") errors.lng = "Enter the longitude too, or clear both fields.";
  if (errors.lat !== undefined || errors.lng !== undefined) return { kind: "invalid", ...errors };
  if (lat.kind !== "value" || lng.kind !== "value") return { kind: "invalid" };
  return { kind: "position", lat_micro: lat.micro, lng_micro: lng.micro };
}

/** "19.076090, 72.877656", optionally with the fix's accuracy: "(within 12 m)". */
export function formatCoordinates(latMicro: number, lngMicro: number, accuracyM?: number | null): string {
  const base = `${microToDegrees(latMicro).toFixed(6)}, ${microToDegrees(lngMicro).toFixed(6)}`;
  return accuracyM === null || accuracyM === undefined ? base : `${base} (within ${accuracyM} m)`;
}
