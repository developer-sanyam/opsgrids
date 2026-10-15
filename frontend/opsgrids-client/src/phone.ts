/**
 * Phone numbers, as a person types and reads them.
 *
 * The server stores and accepts E.164 only (`+919000000012`) and refuses a number with no country
 * code rather than guessing one -- a wrong guess messages a stranger. What it cannot know is the
 * organization's own country, and for an Indian organization a bare ten-digit mobile
 * (`9000000012`, `09000000012`, `919000000012`) is unambiguous. This module is where that
 * knowledge lives: it turns what a rep typed or pasted into E.164 for an organization whose
 * default country is India, and refuses -- never guesses -- for any other.
 *
 * Display is the other half and lives in `@opsgrids/ui` (`formatPhone`), which this package does
 * not depend on.
 */

/** E.164: a leading `+`, a non-zero country digit, 8 to 15 digits in all. The server's rule. */
const E164 = /^\+[1-9]\d{7,14}$/;
/** An Indian mobile subscriber number: ten digits, starting 6-9. */
const IN_MOBILE = /^[6-9]\d{9}$/;

let defaultRegion: string | null = "IN";

/** The country a bare national number is read in (ISO 3166-1 alpha-2), or `null` for "none: always
 *  require a `+`". Set from the organization's own country once it is known; India until then. */
export function setDefaultPhoneRegion(country: string | null | undefined): void {
  defaultRegion = country === undefined || country === null || country.trim() === "" ? null : country.trim().toUpperCase();
}

export function getDefaultPhoneRegion(): string | null {
  return defaultRegion;
}

/**
 * `raw` as an E.164 number, or `null` when it cannot be read as one without guessing.
 *
 * Always accepted: `+` then a valid country code and number, with spaces, dashes, dots and
 * brackets stripped; and `00` as a spelling of `+`. For region `IN` only, a national number is
 * also accepted and given `+91`: ten digits starting 6-9, the same behind a trunk `0`, and the
 * same behind a bare `91`. Nothing else is ever completed.
 */
export function normalisePhone(raw: string, region: string | null = defaultRegion): string | null {
  const compact = raw.trim().replace(/[\s\-().]/g, "");
  if (compact === "") return null;

  if (compact.startsWith("+")) return E164.test(compact) ? compact : null;
  if (!/^\d+$/.test(compact)) return null;
  if (compact.startsWith("00")) {
    const international = `+${compact.slice(2)}`;
    return E164.test(international) ? international : null;
  }

  if (region === "IN") {
    if (IN_MOBILE.test(compact)) return `+91${compact}`;
    if (compact.length === 11 && compact.startsWith("0") && IN_MOBILE.test(compact.slice(1))) {
      return `+91${compact.slice(1)}`;
    }
    if (compact.length === 12 && compact.startsWith("91") && IN_MOBILE.test(compact.slice(2))) {
      return `+${compact}`;
    }
  }
  return null;
}
