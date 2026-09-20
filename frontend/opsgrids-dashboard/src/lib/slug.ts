/** `Slug` from `account/app/schemas.py`: lowercase, digits, hyphens, 2–60, no leading hyphen. */
export const SLUG_RULE = /^[a-z0-9][a-z0-9-]{1,59}$/;

/**
 * A slug a person would have typed, from the organisation's name. Accents are folded to their
 * base letters (NFKD, then the combining marks dropped) before anything else is replaced, so
 * "Ünïcode" becomes "unicode" rather than a run of hyphens. Editable afterwards.
 */
/**
 * What a keystroke may leave in the handle field: lowercase, invalid characters folded to a
 * hyphen, runs collapsed. Unlike `suggestSlug` this keeps a trailing hyphen, because the person
 * is mid-word ("green-" on the way to "green-leaf"); `suggestSlug` trims it at submit time.
 */
export function sanitiseHandleInput(raw: string): string {
  return raw
    .toLowerCase()
    .normalize("NFKD")
    .replace(/\p{M}+/gu, "")
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+/, "")
    .slice(0, 60);
}

export function suggestSlug(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/\p{M}+/gu, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}
