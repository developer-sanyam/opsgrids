/**
 * Spread-only-when-present, for an optional prop under `exactOptionalPropertyTypes`.
 *
 * `error?: string` on a design-system field means "either the key is absent, or it's a string" —
 * explicitly passing `error={undefined}` is a type error under strict mode, and every field
 * error in this app is naturally `string | undefined` (a local check `??` the server's
 * `fieldErrors[name]`). `{...maybe("error", value)}` is the one-line spread that satisfies both:
 * present with a value, or not present at all.
 */
export function maybe<K extends string, V>(
  key: K,
  value: V | undefined,
): { [P in K]: V } | Record<string, never> {
  return value === undefined ? {} : ({ [key]: value } as { [P in K]: V });
}
