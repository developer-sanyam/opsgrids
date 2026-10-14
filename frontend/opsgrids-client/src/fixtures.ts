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

/** The page envelope every list endpoint returns, built in memory for fixture mode. */
export interface FixturePage<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

export interface PaginateFixtureOptions {
  /**
   * What `pages` reads when the list is empty. The gateway's list endpoints report `0`, which is
   * the default; the storefront's own fixtures have always reported `1` (one empty page).
   */
  pagesWhenEmpty?: 0 | 1;
}

/** Slice an in-memory list into the page envelope the gateway returns. `page` is 1-based. */
export function paginateFixture<T>(
  items: T[],
  page: number,
  size: number,
  { pagesWhenEmpty = 0 }: PaginateFixtureOptions = {},
): FixturePage<T> {
  const total = items.length;
  const pages = total === 0 ? pagesWhenEmpty : Math.ceil(total / size);
  const start = (page - 1) * size;
  return { items: items.slice(start, start + size), total, page, size, pages };
}
