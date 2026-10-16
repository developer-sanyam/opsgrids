/** Merge fields the service can fill for any contact (`domain.MERGE_BUILTINS`). Anything else
 *  must be an attribute key some contact carries, or the service refuses to send. */
const STANDARD = new Set([
  "first_name", "last_name", "name", "full_name", "customer_name", "email", "company", "company_name",
]);

const SAMPLE: Record<string, string> = {
  first_name: "Priya",
  last_name: "Sharma",
  name: "Priya Sharma",
  full_name: "Priya Sharma",
  customer_name: "Priya Sharma",
  email: "priya@example.com",
  company: "Acme Traders",
  company_name: "Acme Traders",
};

const FIELD = /\{\{\s*([a-zA-Z0-9_.]+)\s*(?:\|([^{}]*))?\}\}/g;

/** Field names used in the given texts that are not standard, lower-cased and sorted. */
export function nonStandardFields(...texts: string[]): string[] {
  const found = new Set<string>();
  for (const text of texts) {
    for (const m of text.matchAll(FIELD)) {
      const name = (m[1] ?? "").toLowerCase();
      if (!STANDARD.has(name)) found.add(name);
    }
  }
  return [...found].sort();
}

/** The text as a sample recipient would get it: standard fields filled, the rest by fallback. */
export function sampleFill(text: string): string {
  return text.replace(FIELD, (_all, name: string, fallback?: string) =>
    SAMPLE[name.toLowerCase()] ?? (fallback ?? "").trim(),
  );
}
