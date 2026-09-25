// Writes opsgrids-config/token-baseline.json: per file, how many `opsgrids/tokens` violations of
// each category it has today. The lint rule holds every file to its number.
//
//   pnpm tokens:baseline                   lower the numbers after a fix (the usual reason to run it)
//   pnpm tokens:baseline --allow-increase  also raise them — for a reviewer to see and question
//
// Without `--allow-increase` it refuses to raise any count or add any file, and writes nothing:
// the baseline is a ratchet, and a ratchet that turns both ways is a suggestion.

import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { ESLint } from "eslint";
import tseslint from "typescript-eslint";

import { TOKENS_OPTIONS } from "../eslint.preset.js";
import tokens, { CATEGORIES, DEFAULT_BASELINE, FRONTEND_ROOT, countViolations } from "../rules/tokens.js";

const allowIncrease = process.argv.includes("--allow-increase");

/** Every workspace package `pnpm -r lint` walks: a directory with its own eslint.config.js. */
const packages = readdirSync(FRONTEND_ROOT, { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && existsSync(path.join(FRONTEND_ROOT, entry.name, "eslint.config.js")))
  .map((entry) => entry.name)
  .sort();

const eslint = new ESLint({
  cwd: FRONTEND_ROOT,
  overrideConfigFile: true,
  overrideConfig: [
    {
      ignores: [
        "**/node_modules/**",
        "**/dist/**",
        "**/dist-buyer/**",
        "**/storybook-static/**",
        "**/test-results/**",
        "**/playwright-report/**",
      ],
    },
    {
      files: ["**/*.{ts,tsx}"],
      ignores: ["**/*.{test,spec}.{ts,tsx}", "**/test-setup.{ts,tsx}"],
      languageOptions: {
        parser: tseslint.parser,
        parserOptions: { ecmaFeatures: { jsx: true } },
      },
      linterOptions: { reportUnusedDisableDirectives: "off" },
      plugins: { opsgrids: { rules: { tokens } } },
      rules: {
        // An empty baseline, so every counted violation is reported and can be tallied.
        "opsgrids/tokens": ["error", { ...TOKENS_OPTIONS, baseline: path.join(FRONTEND_ROOT, ".no-token-baseline") }],
      },
    },
  ],
});

const results = await eslint.lintFiles(packages.map((name) => `${name}/**/*.{ts,tsx}`));

const next = {};
for (const result of results) {
  const counts = countViolations(result.messages);
  if (Object.keys(counts).length === 0) continue;
  const relative = path.relative(FRONTEND_ROOT, result.filePath).split(path.sep).join("/");
  next[relative] = Object.fromEntries(CATEGORIES.filter((c) => counts[c]).map((c) => [c, counts[c]]));
}

const previous = existsSync(DEFAULT_BASELINE) ? JSON.parse(readFileSync(DEFAULT_BASELINE, "utf8")) : {};
const increases = [];
for (const [file, counts] of Object.entries(next)) {
  for (const [category, count] of Object.entries(counts)) {
    const was = previous[file]?.[category] ?? 0;
    if (count > was) increases.push(`  ${file}  ${category}: ${was} → ${count}`);
  }
}

if (increases.length > 0 && !allowIncrease && Object.keys(previous).length > 0) {
  console.error("These counts would go up. Fix the new violations, or rerun with --allow-increase");
  console.error("and justify it in review:\n");
  console.error(increases.join("\n"));
  process.exit(1);
}

const sorted = Object.fromEntries(Object.keys(next).sort().map((file) => [file, next[file]]));
writeFileSync(DEFAULT_BASELINE, `${JSON.stringify(sorted, null, 2)}\n`);

const totals = Object.fromEntries(CATEGORIES.map((category) => [category, 0]));
for (const counts of Object.values(sorted)) {
  for (const [category, count] of Object.entries(counts)) totals[category] += count;
}
const removed = Object.keys(previous).filter((file) => !(file in sorted)).length;
console.log(
  `token-baseline.json: ${Object.keys(sorted).length} files, ` +
    CATEGORIES.map((category) => `${category} ${totals[category]}`).join(", ") +
    (removed > 0 ? `; ${removed} file(s) now clean and dropped` : ""),
);
