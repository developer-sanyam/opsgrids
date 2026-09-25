// `opsgrids/tokens`: feature code draws with the design system's tokens, never around them.
//
// Five things fail, in any string literal or template chunk (class lists, `cn()` arguments,
// variant maps, style objects, SVG attributes):
//
//   colour     a raw colour — `#6c4cf1`, `rgb(…)`, `bg-[#fff]`, a Tailwind palette colour
//              (`bg-blue-500`, `text-white`). Colour comes from console.css: `text-ink`,
//              `bg-surface`, `text-on-accent`.
//   arbitrary  an arbitrary value or property — `text-[0.7rem]`, `max-w-[1400px]`, `[mask:…]`.
//              A size the system lacks is a token or a component variant, added in @opsgrids/ui.
//   spacing    padding, margin, gap or space off the 4px scale: 0 · 1 · 2 · 3 · 4 · 6 · 8 · 12 ·
//              16 (0 · 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 px), or a literal in a `style` object.
//              @opsgrids/ui alone may also use 0.5 and 1.5, for optical alignment inside a control.
//   radius     a radius outside the closed set (rounded-card, -tile, -chip, -full, -none).
//   shadow     a shadow outside the closed set (shadow-card, -float, -overlay, -none).
//
// It ratchets. `token-baseline.json` records, per file, how many of each a file had when the rule
// arrived. A file may not exceed its count — a new violation fails lint — and a file that has
// fewer than its count also fails, until `pnpm tokens:baseline` writes the lower number down, so
// a fix cannot quietly be spent on a new violation later. A file not in the baseline is held to
// zero. `scripts/token-baseline.js` refuses to raise a count unless told to, in review.
//
// Two ways out, both visible in a diff: an `exempt` glob in the preset (data that is not console
// UI, with a reason beside it), or `// eslint-disable-next-line opsgrids/tokens -- <why>` on the
// line. A disabled line is not counted against the file's baseline.

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** `frontend/` — every path in the baseline and in the globs below is relative to it. */
export const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

export const DEFAULT_BASELINE = path.join(FRONTEND_ROOT, "opsgrids-config", "token-baseline.json");

export const CATEGORIES = ["colour", "arbitrary", "spacing", "radius", "shadow"];

const SPACING_SCALE = new Set(["0", "px", "1", "2", "3", "4", "6", "8", "12", "16"]);
const OPTICAL_STEPS = new Set(["0.5", "1.5"]);

const PALETTE =
  "(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|[1-9]00|950)";
const COLOUR_UTILITY =
  "(?:bg|text|border|border-[xytrblse]|ring|ring-offset|outline|fill|stroke|divide|from|via|to|decoration|accent|caret|placeholder|shadow|inset-shadow|inset-ring)";
const PALETTE_CLASS = new RegExp(`^${COLOUR_UTILITY}-(?:white|black|${PALETTE})(?:/\\d+)?$`);
const SPACING_CLASS = /^(?:p|px|py|pt|pr|pb|pl|ps|pe|m|mx|my|mt|mr|mb|ml|ms|me|gap|gap-x|gap-y|space-x|space-y)-(px|\d+(?:\.\d+)?)$/;
const RADIUS_CLASS = /^rounded(?:-(?:t|r|b|l|s|e|tl|tr|br|bl|ss|se|es|ee))?-(?:xs|sm|md|lg|xl|2xl|3xl|4xl)$/;
const SHADOW_CLASS = /^shadow-(?:2xs|xs|sm|md|lg|xl|2xl)$/;
const ARBITRARY_VALUE = /^[a-z][a-z0-9-]*-\[.+\](?:\/[\w.[\]%]+)?$/;

const HEX = /#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})(?![0-9a-z_-])/i;
const WHOLE_HEX = /^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const COLOUR_FUNCTION = /\b(?:rgba?|hsla?|oklch|oklab|hwb|lab|lch)\(/i;

/** SVG and element attributes whose value is a colour. */
const COLOUR_ATTRIBUTES = new Set(["fill", "stroke", "color", "stopColor", "floodColor", "lightingColor"]);
/** `style` keys that take a length on the spacing scale. */
const SPACING_STYLE = /^(?:padding|margin|gap|rowGap|columnGap)(?:Top|Right|Bottom|Left|Block|Inline|BlockStart|BlockEnd|InlineStart|InlineEnd)?$/;

/** The utility part of a class token: variants (`md:`, `hover:`, `[&>svg]:`) and `!` removed. */
export function utilityOf(token) {
  let depth = 0;
  let start = 0;
  for (let i = 0; i < token.length; i += 1) {
    const ch = token[i];
    if (ch === "[" || ch === "(") depth += 1;
    else if (ch === "]" || ch === ")") depth -= 1;
    else if (ch === ":" && depth === 0) start = i + 1;
  }
  let utility = token.slice(start);
  if (utility.startsWith("!")) utility = utility.slice(1);
  if (utility.endsWith("!")) utility = utility.slice(0, -1);
  if (utility.startsWith("-")) utility = utility.slice(1);
  return utility;
}

/**
 * Every violation in one class-list-shaped string, as `{ category, value }`. Pure, so the
 * classification is tested without an ESLint run (`tokens.test.js`).
 */
export function classify(text, { optical = false } = {}) {
  const found = [];
  if (WHOLE_HEX.test(text.trim())) return [{ category: "colour", value: text.trim() }];
  for (const token of text.split(/\s+/)) {
    if (token === "") continue;
    const utility = utilityOf(token);
    if (utility.startsWith("[") && utility.includes(":")) {
      found.push({ category: HEX.test(utility) || COLOUR_FUNCTION.test(utility) ? "colour" : "arbitrary", value: token });
    } else if (ARBITRARY_VALUE.test(utility)) {
      const inside = utility.slice(utility.indexOf("[") + 1, utility.lastIndexOf("]"));
      found.push({ category: HEX.test(inside) || COLOUR_FUNCTION.test(inside) ? "colour" : "arbitrary", value: token });
    } else if (PALETTE_CLASS.test(utility)) {
      found.push({ category: "colour", value: token });
    } else if (RADIUS_CLASS.test(utility)) {
      found.push({ category: "radius", value: token });
    } else if (SHADOW_CLASS.test(utility)) {
      found.push({ category: "shadow", value: token });
    } else {
      const spacing = SPACING_CLASS.exec(utility);
      const step = spacing?.[1];
      if (step !== undefined && !SPACING_SCALE.has(step) && !(optical && OPTICAL_STEPS.has(step))) {
        found.push({ category: "spacing", value: token });
      }
    }
  }
  if (found.length === 0 && COLOUR_FUNCTION.test(text)) found.push({ category: "colour", value: text.trim() });
  return found;
}

/** `**` any depth, `*` within a segment. Enough for the handful of globs in the preset. */
function globToRegExp(glob) {
  let source = "";
  for (let i = 0; i < glob.length; i += 1) {
    const ch = glob[i];
    if (ch === "*" && glob[i + 1] === "*") {
      source += ".*";
      i += 1;
      if (glob[i + 1] === "/") i += 1;
    } else if (ch === "*") source += "[^/]*";
    else source += ch.replace(/[.+?^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${source}$`);
}

const baselineCache = new Map();

function loadBaseline(file) {
  if (!baselineCache.has(file)) {
    baselineCache.set(file, existsSync(file) ? JSON.parse(readFileSync(file, "utf8")) : {});
  }
  return baselineCache.get(file);
}

/** Line numbers an `eslint-disable-*` comment switches this rule off for (or `"all"`). */
function suppressedLines(sourceCode) {
  const lines = new Set();
  for (const comment of sourceCode.getAllComments()) {
    const match = /^\s*eslint-disable(-next-line|-line)?(?:\s+([^-]*?))?\s*(?:--.*)?$/s.exec(comment.value);
    if (match === null) continue;
    const rules = (match[2] ?? "").split(",").map((rule) => rule.trim()).filter(Boolean);
    if (rules.length > 0 && !rules.includes("opsgrids/tokens")) continue;
    if (match[1] === "-next-line") lines.add(comment.loc.end.line + 1);
    else if (match[1] === "-line") lines.add(comment.loc.start.line);
    else return "all";
  }
  return lines;
}

/** One message id per category, so a lint result says which category without parsing text. */
const MESSAGES = {
  colour:
    "Raw colour `{{value}}`: use a colour token from @opsgrids/ui/styles.css (text-ink, bg-surface, text-on-accent…).{{baseline}}",
  arbitrary:
    "Arbitrary value `{{value}}`: use a token or a scale step. A size the system lacks is added to @opsgrids/ui, not written inline.{{baseline}}",
  spacing:
    "Off-scale spacing `{{value}}`: use 0 · 1 · 2 · 3 · 4 · 6 · 8 · 12 · 16 (0–64px on the 4px grid).{{baseline}}",
  radius:
    "Radius `{{value}}` is outside the closed set: rounded-card, rounded-tile, rounded-chip, rounded-full.{{baseline}}",
  shadow:
    "Shadow `{{value}}` is outside the closed set: shadow-card, shadow-float, shadow-overlay.{{baseline}}",
};

/** @type {import("eslint").Rule.RuleModule} */
export default {
  meta: {
    type: "problem",
    docs: { description: "Colour, spacing, radius and shadow come from the design tokens." },
    schema: [
      {
        type: "object",
        properties: {
          baseline: { type: "string" },
          exempt: { type: "array", items: { type: "string" } },
          optical: { type: "array", items: { type: "string" } },
        },
        additionalProperties: false,
      },
    ],
    messages: {
      ...MESSAGES,
      stale:
        "token-baseline.json allows this file {{allowed}} {{category}} violation(s) but it has {{found}}. Lock the improvement in: `pnpm tokens:baseline` from frontend/.",
    },
  },
  create(context) {
    const options = context.options[0] ?? {};
    const relative = path.relative(FRONTEND_ROOT, context.filename).split(path.sep).join("/");
    const matches = (globs) => (globs ?? []).some((glob) => globToRegExp(glob).test(relative));
    if (relative.startsWith("..") || matches(options.exempt)) return {};

    const optical = matches(options.optical);
    const baseline = loadBaseline(options.baseline ?? DEFAULT_BASELINE)[relative] ?? {};
    const sourceCode = context.sourceCode;
    const suppressed = suppressedLines(sourceCode);
    /** category → [{ node, value, counted }] */
    const hits = new Map(CATEGORIES.map((category) => [category, []]));

    function record(node, violations) {
      const counted = suppressed !== "all" && !suppressed.has(node.loc.start.line);
      for (const { category, value } of violations) hits.get(category).push({ node, value, counted });
    }

    function attributeName(node) {
      const parent = node.parent;
      if (parent?.type === "JSXAttribute") return parent.name.name;
      if (parent?.type === "JSXExpressionContainer" && parent.parent?.type === "JSXAttribute") {
        return parent.parent.name.name;
      }
      return undefined;
    }

    function inStyleObject(property) {
      const object = property.parent;
      const container = object?.parent;
      return (
        object?.type === "ObjectExpression" &&
        container?.type === "JSXExpressionContainer" &&
        container.parent?.type === "JSXAttribute" &&
        container.parent.name.name === "style"
      );
    }

    function checkString(node, text) {
      const parent = node.parent;
      if (
        parent?.type === "ImportDeclaration" ||
        parent?.type === "ExportNamedDeclaration" ||
        parent?.type === "ExportAllDeclaration" ||
        parent?.type === "ImportExpression" ||
        (parent?.type === "Property" && parent.key === node) ||
        parent?.type === "TSLiteralType" ||
        parent?.type === "TSExternalModuleReference"
      ) {
        return;
      }
      const violations = classify(text, { optical });
      const attribute = attributeName(node);
      const styleKey = parent?.type === "Property" && parent.value === node && inStyleObject(parent) ? parent.key : undefined;
      const styleName = styleKey?.type === "Identifier" ? styleKey.name : styleKey?.value;
      const colourSlot =
        (attribute !== undefined && COLOUR_ATTRIBUTES.has(attribute)) ||
        (typeof styleName === "string" && /color|background|shadow|fill|stroke|border|outline/i.test(styleName));
      if (violations.length === 0 && colourSlot && HEX.test(text)) {
        violations.push({ category: "colour", value: text });
      }
      if (typeof styleName === "string" && SPACING_STYLE.test(styleName) && /\d(?:px|rem|em)\b/.test(text)) {
        violations.push({ category: "spacing", value: `${styleName}: ${text}` });
      }
      if (violations.length > 0) record(node, violations);
    }

    return {
      Literal(node) {
        if (typeof node.value === "string") checkString(node, node.value);
        else if (
          typeof node.value === "number" &&
          node.value !== 0 &&
          node.parent?.type === "Property" &&
          node.parent.value === node &&
          inStyleObject(node.parent)
        ) {
          const key = node.parent.key;
          const name = key.type === "Identifier" ? key.name : key.value;
          if (typeof name === "string" && SPACING_STYLE.test(name)) {
            record(node, [{ category: "spacing", value: `${name}: ${node.value}` }]);
          }
        }
      },
      TemplateElement(node) {
        checkString(node, node.value.cooked ?? node.value.raw);
      },
      "Program:exit"(program) {
        for (const category of CATEGORIES) {
          const list = hits.get(category);
          const found = list.filter((hit) => hit.counted).length;
          const allowed = baseline[category] ?? 0;
          const over =
            allowed === 0
              ? ""
              : ` This file is baselined at ${allowed} ${category} violation(s) and now has ${found}; the new one has to go.`;
          for (const hit of list) {
            // A disabled line is still reported — ESLint then suppresses it — so its disable
            // comment has something to switch off; an unused directive fails lint on its own.
            if (!hit.counted || found > allowed) {
              context.report({
                node: hit.node,
                messageId: category,
                data: { value: hit.value, baseline: hit.counted ? over : "" },
              });
            }
          }
          if (found < allowed) {
            context.report({
              loc: program.loc.start,
              messageId: "stale",
              data: { allowed: String(allowed), found: String(found), category },
            });
          }
        }
      },
    };
  },
};

/**
 * For `scripts/token-baseline.js`: the counted violations in one file's lint messages, by
 * category. Run against an empty baseline, every counted violation is reported; disabled lines
 * arrive as suppressed messages, which ESLint keeps out of `messages`.
 */
export function countViolations(messages) {
  const counts = {};
  for (const message of messages) {
    if (message.ruleId !== "opsgrids/tokens" || !CATEGORIES.includes(message.messageId)) continue;
    counts[message.messageId] = (counts[message.messageId] ?? 0) + 1;
  }
  return counts;
}
