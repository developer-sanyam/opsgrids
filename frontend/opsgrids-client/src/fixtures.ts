/**
 * The fixtures / live switch.
 *
 * Every product app can render entirely from its own `fixtures.ts` with no gateway and no login —
 * for offline design work and screenshots. Which way the switch defaults is the app's call: a
 * product whose service is a skeleton defaults to fixtures, one whose service ships defaults to
 * live. Either way the decision is dev-only, because `import.meta.env.DEV` compiles to `false` in
 * a production build — a stray CI env var can never ship an app rendering fabricated data with
 * the "Sample data" markers suppressed.
 */

/** A small artificial delay so loading states are exercised in fixtures mode. */
export function settle<T>(value: T, ms = 240): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}
