/** `Slug` from `account/app/schemas.py`: lowercase, digits, hyphens, 2–60, no leading hyphen. */
export const SLUG_RULE = /^[a-z0-9][a-z0-9-]{1,59}$/;

/**
 * A slug a person would have typed, from the organisation's name. Accents are folded to their
 * base letters (NFKD, then the combining marks dropped) before anything else is replaced, so
 * "Ünïcode" becomes "unicode" rather than a run of hyphens. Editable afterwards.
 */
export function suggestSlug(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/\p{M}+/gu, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}
