// The token rule's classification, its ratchet against a baseline, and the baseline file itself.

import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { Linter, RuleTester } from "eslint";
import tseslint from "typescript-eslint";
import { afterAll, describe, expect, it } from "vitest";

import tokens, { CATEGORIES, DEFAULT_BASELINE, FRONTEND_ROOT, classify } from "./tokens.js";

RuleTester.describe = describe;
RuleTester.it = it;
RuleTester.itOnly = it.only;
RuleTester.afterAll = afterAll;

describe("classify", () => {
  const categories = (text, options) => classify(text, options).map((hit) => hit.category);

  it("passes token utilities, the spacing scale and var() shorthands", () => {
    expect(
      categories(
        "bg-surface text-ink p-4 px-6 gap-3 -mt-2 m-auto rounded-card shadow-card text-page md:gap-8 duration-(--duration-quick) size-4.5 h-11",
      ),
    ).toEqual([]);
  });

  it("flags raw colours: hex, colour functions, palette classes and arbitrary colours", () => {
    expect(categories("#6c4cf1")).toEqual(["colour"]);
    expect(categories("0 0 0 1px rgba(0,0,0,.2)")).toEqual(["colour"]);
    expect(categories("text-white bg-blue-500 hover:bg-black/40 bg-[#fff]")).toEqual([
      "colour",
      "colour",
      "colour",
      "colour",
    ]);
  });

  it("flags arbitrary values and properties, and reads through variants and !important", () => {
    expect(categories("text-[0.7rem] md:max-w-[1400px] ![mask-type:luminance] -top-[3px]")).toEqual([
      "arbitrary",
      "arbitrary",
      "arbitrary",
      "arbitrary",
    ]);
    expect(categories("[&>svg]:size-4 data-[state=open]:bg-sunken")).toEqual([]);
  });

  it("holds spacing to the scale, with 0.5 and 1.5 only where optical alignment is allowed", () => {
    expect(categories("p-5 gap-2.5 mt-10 space-y-5 px-1.5")).toEqual([
      "spacing",
      "spacing",
      "spacing",
      "spacing",
      "spacing",
    ]);
    expect(categories("px-1.5 py-0.5", { optical: true })).toEqual([]);
    expect(categories("px-2.5", { optical: true })).toEqual(["spacing"]);
  });

  it("holds radius and shadow to their closed sets", () => {
    expect(categories("rounded-lg rounded-t-xl shadow-md")).toEqual(["radius", "radius", "shadow"]);
    expect(categories("rounded-full rounded-tile shadow-overlay shadow-none")).toEqual([]);
  });

  it("does not mistake prose for classes", () => {
    expect(categories("Amounts are rounded to the nearest paisa, as shown on invoice #12")).toEqual([]);
  });
});

/** A baseline file the tester can point the rule at. */
const dir = mkdtempSync(path.join(tmpdir(), "token-rule-"));
const baseline = path.join(dir, "baseline.json");
const inApp = path.join(FRONTEND_ROOT, "product-demo/src/routes/Page.tsx");
const inUi = path.join(FRONTEND_ROOT, "opsgrids-ui/src/components/Thing.tsx");
writeFileSync(baseline, JSON.stringify({ "product-demo/src/routes/Page.tsx": { spacing: 1 } }));

const tester = new RuleTester({
  languageOptions: { parser: tseslint.parser, parserOptions: { ecmaFeatures: { jsx: true } } },
});

tester.run("opsgrids/tokens", tokens, {
  valid: [
    { code: '<div className="bg-surface p-4" />', filename: inUi, options: [{ baseline }] },
    // Within its baseline: one off-scale spacing is recorded for this file.
    { code: '<div className="p-5" />', filename: inApp, options: [{ baseline }] },
    // Optical steps inside the design system.
    { code: 'cn("gap-1.5 py-0.5")', filename: inUi, options: [{ baseline, optical: ["opsgrids-ui/**"] }] },
    // Exempt paths are not checked at all.
    {
      code: 'const THEME = { ink: "#111111" };',
      filename: path.join(FRONTEND_ROOT, "product-storefront/src/theme/themes/x.ts"),
      options: [{ baseline, exempt: ["product-storefront/src/theme/**"] }],
    },
    // Import paths, object keys and type literals are not class lists.
    { code: 'import x from "./p-5"; const o = { "p-5": 1 }; type T = "text-white";', filename: inUi, options: [{ baseline }] },
  ],
  invalid: [
    {
      code: '<div className="bg-[#fff] p-4" />',
      filename: inUi,
      options: [{ baseline }],
      errors: [{ messageId: "colour" }],
    },
    {
      code: '<svg><path fill="#6c4cf1" /></svg>',
      filename: inUi,
      options: [{ baseline }],
      errors: [{ messageId: "colour" }],
    },
    {
      code: '<div style={{ padding: 12, color: "#abcdef", marginTop: "10px" }} />',
      filename: inUi,
      options: [{ baseline }],
      errors: [{ messageId: "spacing" }, { messageId: "colour" }, { messageId: "spacing" }],
    },
    {
      code: "const cls = `h-11 ${x} text-[0.95rem]`;",
      filename: inUi,
      options: [{ baseline }],
      errors: [{ messageId: "arbitrary" }],
    },
    {
      // Over its baseline of one: every spacing hit in the file is reported, with the count.
      code: '<div className="p-5 gap-2.5" />',
      filename: inApp,
      options: [{ baseline }],
      errors: [
        { messageId: "spacing", data: { value: "p-5", baseline: " This file is baselined at 1 spacing violation(s) and now has 2; the new one has to go." } },
        { messageId: "spacing", data: { value: "gap-2.5", baseline: " This file is baselined at 1 spacing violation(s) and now has 2; the new one has to go." } },
      ],
    },
    {
      // Fewer than its baseline: the improvement has to be written down.
      code: '<div className="p-4" />',
      filename: inApp,
      options: [{ baseline }],
      errors: [{ messageId: "stale" }],
    },
  ],
});

describe("an eslint-disable comment", () => {
  // RuleTester registers the rule under its own name, so a real `opsgrids/tokens` directive is
  // exercised through a Linter with the plugin as the preset registers it.
  function lint(code) {
    const linter = new Linter({ configType: "flat", cwd: FRONTEND_ROOT });
    return linter.verify(
      code,
      [
        {
          files: ["**/*.tsx"],
          languageOptions: { parser: tseslint.parser, parserOptions: { ecmaFeatures: { jsx: true } } },
          linterOptions: { reportUnusedDisableDirectives: "error" },
          plugins: { opsgrids: { rules: { tokens } } },
          rules: { "opsgrids/tokens": ["error", { baseline }] },
        },
      ],
      { filename: inApp },
    );
  }

  it("switches the line off and keeps it out of the file's count", () => {
    // The file is baselined at one spacing violation and none of any other kind: the disabled
    // colour neither fails nor uses up an allowance, and the directive counts as used.
    const messages = lint(
      '// eslint-disable-next-line opsgrids/tokens -- a merchant-supplied colour\nconst c = "#123456";\nexport const X = <div className="p-5" />;',
    );
    expect(messages).toEqual([]);
  });

  it("is itself an error when it switches off nothing", () => {
    const messages = lint(
      '// eslint-disable-next-line opsgrids/tokens -- stale\nconst c = "bg-surface";\nexport const X = <div className="p-5" />;',
    );
    expect(messages.map((m) => m.message)).toEqual([
      expect.stringContaining("Unused eslint-disable directive"),
    ]);
  });
});

describe("token-baseline.json", () => {
  const recorded = JSON.parse(readFileSync(DEFAULT_BASELINE, "utf8"));

  it("names only files that exist", () => {
    const missing = Object.keys(recorded).filter((file) => !existsSync(path.join(FRONTEND_ROOT, file)));
    expect(missing).toEqual([]);
  });

  it("holds only known categories with positive counts, in sorted order", () => {
    for (const counts of Object.values(recorded)) {
      for (const [category, count] of Object.entries(counts)) {
        expect(CATEGORIES).toContain(category);
        expect(count).toBeGreaterThan(0);
      }
    }
    expect(Object.keys(recorded)).toEqual([...Object.keys(recorded)].sort());
  });
});
