/**
 * Build settings every app shares. Plain JS because Vite loads a config through Node, which does
 * not run a workspace package's TypeScript.
 *
 * `assetsInlineLimit`: Vite inlines any asset under 4 kB into the CSS as a `data:` URI. A small
 * @fontsource subset (a unicode-range slice of Manrope) falls under that line, and the edge's
 * Content-Security-Policy is `font-src 'self'`, which refuses `data:` -- so the browser logged a
 * CSP violation on every page and the slice never loaded. Fonts are always emitted as files;
 * everything else keeps Vite's default.
 */

const FONT_FILE = /\.(woff2?|ttf|otf|eot)$/i;

/**
 * @param {string} file
 * @returns {boolean | undefined} `false` to force a file; `undefined` to leave the decision to
 *   Vite's own size rule.
 */
export function assetsInlineLimit(file) {
  return FONT_FILE.test(file) ? false : undefined;
}
