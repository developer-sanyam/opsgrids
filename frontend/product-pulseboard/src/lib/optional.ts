/**
 * A spreadable single-key object, present only when `value` is not `undefined`.
 *
 * `exactOptionalPropertyTypes` (on across this platform's `tsconfig`) treats
 * `<Field error={maybeUndefined} />` as *setting* an optional prop to `undefined`, which is not
 * the same thing as leaving it unset — and the design system's optional props are typed to
 * reject exactly that. `{...withOptional("error", maybeUndefined)}` spreads in the key only when
 * there is a real value, so an absent error is an absent prop, never a prop holding `undefined`.
 */
export function withOptional<K extends string, V>(
  key: K,
  value: V | undefined,
): { [P in K]: V } | Record<string, never> {
  return value !== undefined ? ({ [key]: value } as { [P in K]: V }) : {};
}
