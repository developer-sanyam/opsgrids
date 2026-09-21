/**
 * The one clock this app reads. Fixture mode pins it to the instant the seeded fixtures are
 * written around, so "updated 3h ago", warranty flags and the desk snapshot render
 * deterministically for offline design work. Live mode reads the real clock — a frozen `NOW`
 * reaching production is how every relative timestamp and warranty flag ends up reporting
 * against a date months in the past, forever.
 *
 * Kept as a function, not a constant, so a screen gets a fresh instant on every render and a test
 * can inject one instead.
 *
 * `FIXTURE_NOW` is a plain literal here rather than imported from `@/api/fixtures` on purpose:
 * that module is ~1000 lines of seeded demo data loaded through a dynamic `import()` in
 * `api/client.ts` precisely so a production build never ships it — a static import of anything
 * from it here, on a module every screen reaches through `relativeTime`/`daysUntil`, would pull
 * the whole file back into the bundle regardless of `USE_FIXTURES`.
 */
import { USE_FIXTURES } from "@/api/client";

const FIXTURE_NOW = new Date("2026-08-24T18:00:00Z");

export function now(): Date {
  return USE_FIXTURES ? FIXTURE_NOW : new Date();
}
