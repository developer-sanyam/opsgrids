/**
 * The authored-prose convention, parsed in one place.
 *
 * Fifteen of the thirty-five company/support/legal pages are the same shape: a Privacy Policy, a
 * Terms page, a Shipping Policy, a Returns Policy. All of them are subheadings, paragraphs and
 * lists, in an order the merchant chooses and changes. Numbered settings slots cannot express that
 * — `section7Heading` is unusable at fifteen pages and unusable again the first time a clause is
 * inserted in the middle — so `long-form` takes **one** `textarea` and this module gives it
 * structure.
 *
 * **The convention, in full.** It is deliberately tiny, because a merchant is typing into a plain
 * textarea with no preview and no syntax help:
 *
 * - A line starting `## ` is a subheading.
 * - A line starting `- ` or `* ` is a list item. Consecutive items make one list.
 * - A line starting `1. ` (any digits, then `.` or `)`, then a space) is a numbered item.
 *   Consecutive items make one numbered list — which is what a Terms page's clauses are, and the
 *   reason this one costs its ten lines: `<ol>` is the element, and a merchant typing "1." into a
 *   paragraph would otherwise get a flat run of sentences.
 * - A blank line ends whatever is open. Lines inside a paragraph are joined with a space, so a
 *   merchant's hard-wrapped pasting reflows instead of breaking mid-sentence.
 * - **Anything else is prose.** A single `#`, a stray `>`, a line of asterisks — none of them are
 *   syntax here, and they come out as the characters the merchant typed. This is not Markdown and
 *   must not grow into it: no dependency, no `dangerouslySetInnerHTML`, no inline emphasis. The
 *   output is a list of blocks the renderer turns into real `<h2>`, `<p>`, `<ul>` and `<ol>`.
 *
 * Pure and I/O-free on purpose — it is the part of `long-form` worth testing exhaustively, and it
 * is testable without a DOM.
 */

/** One structural block of authored prose. */
export type LongFormBlock =
  | { kind: "heading"; text: string; slug: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] };

const HEADING = /^##(?:\s+(.*))?$/;
const BULLET = /^[-*]\s+(.*)$/;
/** Digits, then a full stop or a bracket, then a space. "1998 was" is prose; "1. Scope" is a clause. */
const NUMBERED = /^\d+[.)]\s+(.*)$/;

/**
 * A heading's anchor.
 *
 * Plain and human-readable — `#refunds`, not `#lf-3-refunds` — because the whole point of a Terms
 * page's table of contents is that a clause can be linked to from an email, an invoice or another
 * page. That does mean two `long-form` sections on one page whose subheadings share a title would
 * produce the same id twice and the browser would jump to the first; duplicate slugs *within* one
 * section are already suffixed below, and the cross-section case is rare enough to be worth the
 * readable anchor.
 */
export function slugify(text: string): string {
  const slug = text
    .toLowerCase()
    .normalize("NFC")
    // Anything that is not a letter or a digit becomes a separator. `\p{L}` rather than `a-z`, so a
    // Devanagari or accented heading keeps its own characters instead of collapsing to "section".
    // Composed (NFC) first, so "í" is one letter and not a letter plus a combining mark that would
    // itself be treated as punctuation and split the word in half.
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
  return slug === "" ? "section" : slug;
}

/** Parse the whole field. The only entry point a renderer needs. */
export function parseLongForm(text: string): LongFormBlock[] {
  const blocks: LongFormBlock[] = [];
  const seen = new Map<string, number>();

  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  function flushParagraph() {
    if (paragraph.length === 0) return;
    blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
    paragraph = [];
  }

  function flushList() {
    if (list === null) return;
    blocks.push({ kind: "list", ordered: list.ordered, items: list.items });
    list = null;
  }

  function flush() {
    flushParagraph();
    flushList();
  }

  function pushItem(ordered: boolean, item: string) {
    flushParagraph();
    // A bulleted run followed by a numbered run is two lists, not one with a confused marker.
    if (list !== null && list.ordered !== ordered) flushList();
    if (list === null) list = { ordered, items: [] };
    list.items.push(item);
  }

  for (const raw of text.split("\n")) {
    const line = raw.trim();

    if (line === "") {
      flush();
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      const label = heading[1]?.trim() ?? "";
      // `## ` on its own is a merchant mid-edit, not a heading with no words.
      if (label === "") continue;
      const base = slugify(label);
      const count = (seen.get(base) ?? 0) + 1;
      seen.set(base, count);
      blocks.push({ kind: "heading", text: label, slug: count === 1 ? base : `${base}-${count}` });
      continue;
    }

    const bullet = BULLET.exec(line);
    if (bullet && (bullet[1] ?? "").trim() !== "") {
      pushItem(false, (bullet[1] ?? "").trim());
      continue;
    }

    const numbered = NUMBERED.exec(line);
    if (numbered && (numbered[1] ?? "").trim() !== "") {
      pushItem(true, (numbered[1] ?? "").trim());
      continue;
    }

    flushList();
    paragraph.push(line);
  }

  flush();
  return blocks;
}

/** The table of contents: every subheading, in order, with the anchor it was given. */
export function longFormHeadings(blocks: LongFormBlock[]): { text: string; slug: string }[] {
  return blocks
    .filter((block): block is Extract<LongFormBlock, { kind: "heading" }> => block.kind === "heading")
    .map(({ text, slug }) => ({ text, slug }));
}
