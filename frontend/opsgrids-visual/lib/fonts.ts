/**
 * Web fonts, served locally and pinned.
 *
 * Every app (and the workshop) loads its faces from the Google Fonts CDN with a `<link>` in
 * `index.html`. A screenshot that depends on a CDN answering, and on whichever revision of Manrope
 * it answers with today, is not a regression test. So the harness intercepts both Google hosts and
 * answers from the `@fontsource` packages pinned in this package's lockfile: the stylesheet request
 * gets CSS generated here, and each font URL in it gets the woff2 file from `node_modules`.
 *
 * The generated CSS mirrors Google's shape — one `@font-face` per requested weight and unicode
 * subset — rather than one variable-range rule, so weight matching is what production gets: a
 * `font-weight: 300` on a page that only asked for 400–800 still falls back to 400, as it does live.
 *
 * A family nobody has vendored fails loudly. A page silently falling back to a system face would
 * produce a baseline of the wrong font and pass forever.
 */

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);

/** Where the harness pretends font files live. Any path under the real host would do. */
export const FONT_FILE_PREFIX = "https://fonts.gstatic.com/opsgrids-visual/";

interface VendoredFamily {
  /** The package in this workspace's lockfile. */
  pkg: string;
  /** The stylesheet inside it for a weight: variable fonts have one file, static fonts one per weight. */
  cssFor: (weight: number) => string;
}

const FAMILIES: Record<string, VendoredFamily> = {
  Manrope: { pkg: "@fontsource-variable/manrope", cssFor: () => "wght.css" },
  "IBM Plex Mono": { pkg: "@fontsource/ibm-plex-mono", cssFor: (weight) => `${weight}.css` },
};

function packageDir(pkg: string): string {
  return dirname(require.resolve(`${pkg}/package.json`));
}

interface Face {
  file: string;
  unicodeRange: string;
}

/** The subsets a fontsource stylesheet declares: which woff2 file covers which code points. */
function facesIn(css: string): Face[] {
  const faces: Face[] = [];
  for (const block of css.split("@font-face").slice(1)) {
    const file = /url\(\.\/files\/([^)]+\.woff2)\)/.exec(block)?.[1];
    const unicodeRange = /unicode-range:\s*([^;]+);/.exec(block)?.[1];
    if (file && unicodeRange) faces.push({ file, unicodeRange: unicodeRange.trim() });
  }
  return faces;
}

/** `family=Manrope:wght@400;500&family=IBM+Plex+Mono:wght@400` → families with their weights. */
export function requestedFamilies(url: URL): { family: string; weights: number[] }[] {
  return url.searchParams.getAll("family").map((spec) => {
    const [family = "", axes = ""] = spec.split(":");
    const weights = (axes.split("@")[1] ?? "400")
      .split(";")
      .map(Number)
      .filter((weight) => Number.isInteger(weight));
    return { family, weights: weights.length > 0 ? weights : [400] };
  });
}

/** The stylesheet Google would have answered with, pointing at the vendored files instead. */
export function stylesheetFor(url: URL): string {
  const rules: string[] = [];
  for (const { family, weights } of requestedFamilies(url)) {
    const vendored = FAMILIES[family];
    if (!vendored) {
      throw new Error(
        `The page asked Google Fonts for "${family}", which the visual harness has not vendored. ` +
          `Add its @fontsource package to opsgrids-visual and to FAMILIES in lib/fonts.ts.`,
      );
    }
    const dir = packageDir(vendored.pkg);
    for (const weight of weights) {
      const css = readFileSync(join(dir, vendored.cssFor(weight)), "utf8");
      for (const face of facesIn(css)) {
        rules.push(
          [
            "@font-face {",
            `  font-family: '${family}';`,
            "  font-style: normal;",
            `  font-weight: ${weight};`,
            "  font-display: swap;",
            `  src: url(${FONT_FILE_PREFIX}${vendored.pkg}/files/${face.file}) format('woff2');`,
            `  unicode-range: ${face.unicodeRange};`,
            "}",
          ].join("\n"),
        );
      }
    }
  }
  return rules.join("\n");
}

/** The bytes behind a URL `stylesheetFor` produced. */
export function fontFile(url: URL): Buffer {
  const rest = url.href.slice(FONT_FILE_PREFIX.length);
  const pkg = Object.values(FAMILIES).find((family) => rest.startsWith(`${family.pkg}/files/`))?.pkg;
  if (!pkg) throw new Error(`No vendored font behind ${url.href}`);
  const file = rest.slice(`${pkg}/files/`.length);
  if (file.includes("/") || file.includes("..")) throw new Error(`Refusing font path ${file}`);
  return readFileSync(join(packageDir(pkg), "files", file));
}
