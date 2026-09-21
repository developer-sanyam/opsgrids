/**
 * `exactOptionalPropertyTypes` treats `{ error: undefined }` as a different shape from `{}` —
 * an optional prop typed `string?` refuses a `string | undefined` expression outright, even
 * though the value itself would be fine at runtime. Every field error in this app is exactly
 * that shape (`fieldErrors.foo` is `string | undefined` under `noUncheckedIndexedAccess`), so
 * spreading through this rather than passing the prop directly is what makes the two flags agree.
 */
export function optionalProp<K extends string, V>(key: K, value: V | undefined): { [P in K]: V } | Record<string, never> {
  return value === undefined ? {} : ({ [key]: value } as { [P in K]: V });
}
