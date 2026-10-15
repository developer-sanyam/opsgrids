/**
 * The one clock this app reads from. Every live screen that needs "today" — defaulting the
 * audience "Defined on" date, validating a flight window — calls `today()`, never a frozen
 * constant. Fixture mode is the one exception: `FIXTURE_TODAY` (`api/fixtures.ts`) pins the clock
 * to the date the seed data was written against, so the illustrative screens stay internally
 * consistent; `client.ts`'s `USE_FIXTURES` flag decides which one a screen should ask for.
 */

import { orgToday } from "@opsgrids/client";

/** Today, as an ISO calendar date (`yyyy-mm-dd`), read from the real clock. */
export function today(): string {
  return orgToday();
}
