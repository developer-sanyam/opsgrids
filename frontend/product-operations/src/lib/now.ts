/**
 * The one clock this app reads from. Every live screen that needs "today" (PO lateness, the
 * needs-attention queue, defaulting a date field) calls `today()` — never a frozen constant.
 *
 * Fixture mode is the one exception: `FIXTURE_TODAY` (from `api/fixtures.ts`) pins the clock to
 * the date the seed data was written against, so the illustrative screens stay internally
 * consistent. `today()` itself is always the real date; `client.ts`'s `USE_FIXTURES` flag decides
 * which one a screen should ask for, exactly as it does for every other read.
 */

/** Today, as an ISO calendar date (`yyyy-mm-dd`), read from the real clock. */
export function today(): string {
  return new Date().toISOString().slice(0, 10);
}
