/**
 * The query string every list call builds from its filter object.
 */

export type QueryValue = string | number | boolean | null | undefined;

export interface BuildQueryOptions {
  /**
   * Keep a parameter whose value is the empty string (`?q=`). Off by default: an empty filter is
   * the same as no filter, and the apps drop it. PulseBoard has always sent it.
   */
  keepEmpty?: boolean;
}

/**
 * `{ q: "acme", page: 2, archived: false }` becomes `?q=acme&page=2&archived=false`; no parameters
 * left is `""`, not `"?"`.
 *
 * - `undefined` and `null` are skipped — a filter that is not set is not sent. (`null` was
 *   skipped by finance only; the other callers' types never allowed it, so nothing else changes.)
 * - `""` is skipped unless `keepEmpty`.
 * - `false` and `0` are real values and are sent.
 */
export function buildQuery(params: Record<string, QueryValue>, options: BuildQueryOptions = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    if (value === "" && options.keepEmpty !== true) continue;
    query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `?${text}` : "";
}
