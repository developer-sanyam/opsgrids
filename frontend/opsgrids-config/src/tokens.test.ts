/**
 * The design-token rules, run through the same flat config an app runs them under — the shared
 * preset with the token blocks after it — over source strings, and asserted on what they report.
 *
 * A lint rule is easy to get wrong in both directions and neither shows up as a failure: too loose
 * and a raw value gets through with everything green, too eager and people learn to suppress it.
 * So each thing it must refuse and each thing it must leave alone is a case here, including the
 * ones that look like a violation and are not (a support-case number that happens to be four hex
 * digits, a Tailwind variant that takes brackets, an SVG's coordinates).
 */

import { Linter } from "eslint";
import { describe, expect, it } from "vitest";

import preset from "../eslint.preset.js";
import tokens from "../eslint.tokens.js";

const linter = new Linter({ configType: "flat" });
const config = [...preset, ...tokens];

/** The rule messages a source string earns, linted as if it lived at `filename`. */
function report(code: string, filename = "src/routes/Example.tsx"): string[] {
  return linter
    .verify(code, config, { filename })
    .filter((message) => message.ruleId === "no-restricted-syntax")
    .map((message) => message.message);
}

const jsx = (attributes: string) => `export const X = () => <div ${attributes} />;`;

describe("raw colours", () => {
  it("refuses a hex colour in a class, an attribute or a plain string", () => {
    for (const code of [
      jsx(`className="bg-[#6c4cf1]"`),
      jsx(`fill="#ffffff"`),
      `export const COLOUR = "#fff";`,
      `export const COLOUR = "#FFFFFF80";`,
      `export const TINT = "bg-[#e7eef7] text-[#2a4a6b]";`,
    ]) {
      const found = report(code);
      expect(found.length, code).toBeGreaterThan(0);
      expect(found[0], code).toContain("raw colour");
    }
  });

  it("refuses a colour function", () => {
    for (const code of [
      `export const S = "rgba(108,76,241,0.22)";`,
      `export const S = "rgb(0 0 0 / 50%)";`,
      "export const S = `hsl(${hue} 50% 50%)`;",
      `export const S = "oklch(60% 0.2 280)";`,
    ]) {
      expect(report(code).join(" "), code).toContain("raw colour");
    }
  });

  it("refuses a colour inside a template literal", () => {
    expect(report("export const S = `bg-[#fff] ${x}`;").join(" ")).toContain("raw colour");
  });

  it("leaves tokens alone: a Tailwind colour class and a custom property", () => {
    expect(report(jsx(`className="bg-accent text-muted border-line bg-surface text-white"`))).toEqual([]);
    expect(report(jsx(`fill="var(--color-accent)" stroke="currentColor"`))).toEqual([]);
  });

  it("does not mistake a number that follows a hash for a colour", () => {
    // Support-case #4821, PR #1234: four digits and no letter is a reference, not #4821 the colour.
    expect(report(`export const S = "Unmasked billing contact for support case #4821";`)).toEqual([]);
    expect(report(`export const S = "See #123.";`)).toEqual([]);
    // Six digits is a colour wherever it is.
    expect(report(`export const S = "#123456";`).length).toBeGreaterThan(0);
  });

  it("does not flag an anchor or an id that starts with a hash and a word", () => {
    expect(report(`export const HREF = "#main-content";`)).toEqual([]);
    expect(report(jsx(`href="#main-content"`))).toEqual([]);
  });
});

describe("Tailwind arbitrary values", () => {
  it("refuses a bracketed value for any utility", () => {
    for (const cls of ["w-[256px]", "text-[0.6rem]", "p-[7px]", "max-w-[1760px]", "sm:text-[2.1rem]", "h-[calc(100dvh-2rem)]", "-mt-[3px]", "hover:bg-[var(--x)]"]) {
      const found = report(jsx(`className="flex ${cls}"`));
      expect(found.join(" "), cls).toContain("arbitrary value");
    }
  });

  it("refuses one in a class built from a template", () => {
    expect(report("export const C = `size-[18px] ${y}`;").join(" ")).toContain("arbitrary value");
  });

  it("names the way out: the scale, or a custom property", () => {
    const [message] = report(jsx(`className="w-[256px]"`));
    expect(message).toContain("4 · 8 · 12 · 16 · 24 · 32 · 48 · 64");
    expect(message).toContain("w-(--share)");
  });

  it("leaves the scale and the custom-property shorthand alone", () => {
    expect(report(jsx(`className="w-64 max-w-7xl gap-3 p-6 text-xs w-(--share) left-(--x) rounded-tile"`))).toEqual([]);
  });

  it("leaves a variant that legitimately takes brackets alone", () => {
    expect(report(jsx(`className="data-[state=open]:bg-accent aria-[sort=ascending]:text-ink group-hover:text-ink"`))).toEqual([]);
  });
});

describe("raw lengths", () => {
  it("refuses a length with a unit in a string", () => {
    for (const code of [`export const W = "12px";`, `export const W = "1.5rem";`, `export const W = "50vh";`, `export const W = "100dvh";`]) {
      expect(report(code).join(" "), code).toContain("raw length");
    }
  });

  it("leaves a plain number, a version, a date and a percentage alone", () => {
    for (const code of [
      `export const N = "12";`,
      `export const N = "v2";`,
      `export const N = "2026-08-24T00:00:00Z";`,
      `export const N = "40%";`,
      `export const N = "0 0 24 24";`,
      `export const N = "1.7";`,
    ]) {
      expect(report(code), code).toEqual([]);
    }
  });

  it("does not read a number in code as a length — an SVG's coordinates are geometry", () => {
    expect(report(`export const G = { width: 640, height: 200, PAD_Y: 16 };`)).toEqual([]);
  });
});

describe("inline styles", () => {
  it("refuses a style that sets a real CSS property", () => {
    for (const attributes of [
      `style={{ width: "40%" }}`,
      `style={{ backgroundColor: color }}`,
      `style={{ "box-shadow": "none" }}`,
      `style={{ left: x, top: y }}`,
    ]) {
      expect(report(jsx(attributes)).join(" "), attributes).toContain("inline style");
    }
  });

  it("allows a style that only sets custom properties, which a class then reads", () => {
    for (const attributes of [
      `style={{ "--share": "40%" }}`,
      `style={{ "--i": i }}`,
      `style={{ "--x": "10%", "--y": "20%" } as CSSProperties}`,
    ]) {
      expect(report(jsx(attributes)), attributes).toEqual([]);
    }
  });

  it("refuses a real property mixed in with a custom one", () => {
    expect(report(jsx(`style={{ "--share": "40%", width: "40%" }}`)).join(" ")).toContain("inline style");
  });

  it("says what to do instead", () => {
    const [message] = report(jsx(`style={{ width: w }}`));
    expect(message).toContain("custom property");
  });
});

describe("where it applies", () => {
  it("applies to an app's own source", () => {
    expect(report(`export const C = "#fff";`, "src/ui/Thing.tsx").length).toBeGreaterThan(0);
    expect(report(`export const C = "#fff";`, "src/lib/palette.ts").length).toBeGreaterThan(0);
  });

  it("does not apply to tests, which assert on classes and carry fixtures", () => {
    expect(report(jsx(`className="bg-[#fff]"`), "src/ui/Thing.test.tsx")).toEqual([]);
    expect(report(`export const C = "w-[10px]";`, "src/ui/Thing.spec.ts")).toEqual([]);
  });

  it("does not apply to test helpers", () => {
    for (const filename of ["src/testing.ts", "src/testing-render.tsx", "src/test-setup.ts", "src/contracts.ts"]) {
      expect(report(`export const C = "#fff";`, filename), filename).toEqual([]);
    }
  });

  it("does not apply outside src, where a config file may spell a value", () => {
    expect(report(`export default { colour: "#fff" };`, "vite.config.ts")).toEqual([]);
  });
});
