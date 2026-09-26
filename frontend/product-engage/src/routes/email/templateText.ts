/**
 * The pure text handling behind the email template editor: placeholders, insertion at the caret,
 * and the sandboxed preview document. No React, no I/O, so each rule is testable on its own.
 */

/** The service's placeholder grammar (`_PLACEHOLDER` in `domain.py`): `{{ name }}`, where a name is
 *  letters, digits, underscores and dots. Kept identical so a variable this editor inserts is one
 *  the service recognises. */
const PLACEHOLDER = /\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}/g;
const VARIABLE_NAME = /^[a-zA-Z0-9_.]+$/;

export function isValidVariableName(name: string): boolean {
  return VARIABLE_NAME.test(name);
}

export function placeholderToken(name: string): string {
  return `{{${name}}}`;
}

/** Every distinct placeholder named in the text, in order of first appearance. */
export function placeholdersIn(text: string): string[] {
  const seen = new Set<string>();
  for (const match of text.matchAll(PLACEHOLDER)) {
    const name = match[1];
    if (name !== undefined) seen.add(name);
  }
  return [...seen];
}

/** `insert` written over the selection `start..end` — the caret's position after it is returned so
 *  the field can put the caret back where the person was typing. */
export function insertAtSelection(
  value: string,
  start: number,
  end: number,
  insert: string,
): { value: string; caret: number } {
  const from = Math.min(start, end);
  const to = Math.max(start, end);
  return { value: value.slice(0, from) + insert + value.slice(to), caret: from + insert.length };
}

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => ESCAPES[char] ?? char);
}

/** Escaped first, so nothing a person typed can become markup, and only then are the placeholders
 *  found and marked — the `{{` and `}}` characters are not touched by escaping. */
function markPlaceholders(text: string): string {
  return escapeHtml(text).replace(PLACEHOLDER, (_whole, name: string) => `<mark>{{${name}}}</mark>`);
}

/**
 * The document the preview frame shows: the subject and the body exactly as written, with each
 * placeholder highlighted, as plain text.
 *
 * The body is plain text because that is what the service sends for a template — it is set as the
 * message's text part — so a preview that rendered tags would show something recipients never see.
 * Every character is escaped, the frame is sandboxed with no permissions, and a content security
 * policy forbids everything but inline style: three independent reasons a script in a template body
 * cannot run in this page, so no one of them is load-bearing alone.
 */
export function previewDocument(subject: string, body: string): string {
  return [
    "<!doctype html>",
    '<html lang="en"><head><meta charset="utf-8">',
    `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">`,
    "<style>",
    "body{font-family:system-ui,sans-serif;line-height:1.5;margin:1rem}",
    "h1{font-size:1.1rem;margin:0 0 1rem}",
    "p{white-space:pre-wrap;margin:0}",
    "mark{font-weight:700}",
    "</style></head><body>",
    `<h1>${markPlaceholders(subject)}</h1>`,
    `<p>${markPlaceholders(body)}</p>`,
    "</body></html>",
  ].join("");
}
