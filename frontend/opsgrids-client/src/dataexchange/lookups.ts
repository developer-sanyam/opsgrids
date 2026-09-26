import type { ExportPage, Lookup } from "./types";

function normalise(label: string): string {
  return label.trim().replace(/\s+/g, " ").toLowerCase();
}

/**
 * A `Lookup` over `(label, value)` pairs. Matching ignores case and runs of spaces. A label that
 * more than one entry carries is ambiguous: `resolve` returns undefined rather than pick one, so
 * the row is refused instead of landing on the wrong record.
 */
export function makeLookup(entries: readonly { label: string; value: string }[]): Lookup {
  const byLabel = new Map<string, string | null>();
  for (const { label, value } of entries) {
    const key = normalise(label);
    byLabel.set(key, byLabel.has(key) ? null : value);
  }
  return {
    labels: entries.map((e) => e.label),
    resolve: (label) => byLabel.get(normalise(label)) ?? undefined,
    isAmbiguous: (label) => byLabel.get(normalise(label)) === null,
  };
}

/**
 * Every row of a paged list, for reference data and the export. The screens' own "directory"
 * loads stop at one page of 200, which silently truncates a larger organisation, so a lookup built
 * from them would call a real company unknown.
 *
 * Stops at `maxPages` (default 50, so 10 000 rows at the default size) or when the server says
 * there are no more pages. Rejects with the request's own error; honours `signal`.
 */
export async function collectPages<T>(
  fetchPage: (page: number, size: number) => Promise<ExportPage<T>>,
  options: { size?: number; maxPages?: number; signal?: AbortSignal } = {},
): Promise<T[]> {
  const size = Math.min(options.size ?? 200, 200);
  const maxPages = options.maxPages ?? 50;
  const rows: T[] = [];
  for (let page = 1; page <= maxPages; page += 1) {
    options.signal?.throwIfAborted();
    const result = await fetchPage(page, size);
    rows.push(...result.items);
    if (page >= result.pages || result.items.length === 0) break;
  }
  return rows;
}
