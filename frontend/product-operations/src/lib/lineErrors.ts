/**
 * Sorting a server's field errors onto the line rows they belong to.
 *
 * The same field arrives spelled two ways: the API's own checks say `lines[2].from_location_id`,
 * and request validation says `lines.2.quantity`. Both mean "line 3's quantity". A line-level
 * failure with no field of its own (a duplicate line, "nothing to record") comes back under the
 * key `line`.
 */
export function lineFieldErrors(
  fieldErrors: Readonly<Record<string, string>>,
  collection: string,
  index: number,
): Record<string, string> {
  const bracket = `${collection}[${index}]`;
  const dotted = `${collection}.${index}`;
  const found: Record<string, string> = {};
  for (const [key, message] of Object.entries(fieldErrors)) {
    for (const prefix of [bracket, dotted]) {
      if (key === prefix) found.line = message;
      else if (key.startsWith(`${prefix}.`)) found[key.slice(prefix.length + 1)] = message;
    }
  }
  return found;
}
