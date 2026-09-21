/**
 * `exactOptionalPropertyTypes` (frontend/CLAUDE.md) means an optional prop like `error?: string`
 * may be **omitted**, but never explicitly set to `undefined` — so `error={maybeUndefined}` does
 * not typecheck where `maybeUndefined` is `string | undefined`. This spreads the prop in only
 * when there is a value, the same shape `FormDialog`'s own `error`/`reference` handling uses.
 */
export function errorProp(value: string | undefined): { error: string } | Record<string, never> {
  return value === undefined ? {} : { error: value };
}
