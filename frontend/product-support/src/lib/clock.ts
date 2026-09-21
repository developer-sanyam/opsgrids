/**
 * The one clock this app reads. Fixture mode pins it to the instant the seeded fixtures are
 * written around, so SLA countdowns and "needs attention" render deterministically for offline
 * design work. Live mode reads the real clock — a frozen `NOW` reaching production is how every
 * SLA pill, the breaching KPI and "needs attention" end up reporting against a date months in
 * the past.
 *
 * Kept as a function, not a constant, so a screen that wants a fresh instant on every render (or
 * a test that wants to inject one) can — `now()` is only ever a default parameter value, never a
 * module-level snapshot taken once at import time.
 */
import { USE_FIXTURES } from "@/api/client";
import { FIXTURE_NOW } from "@/api/fixtures";

export function now(): Date {
  return USE_FIXTURES ? FIXTURE_NOW : new Date();
}
