/**
 * What no line of the operator console may contain, run as a test for the reason `isolation.test.ts`
 * is one: a lint rule nobody has watched fail is a rule nobody knows still works.
 *
 * `eslint.config.js` refuses the calls that turn a string into script or markup, the browser's own
 * modal prompts, and `console`. The rule they share with the design-token lint — `no-restricted-syntax`
 * takes one list per config block — is where the mistake would be quiet: a block that set it on its
 * own would replace the token selectors, and the token lint would stop firing with nothing to say so.
 * The last group below is that pin.
 */

import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

/** ESLint reads this package's own `eslint.config.js`, exactly as `pnpm lint` does. */
const eslint = new ESLint({ cwd: process.cwd() });

/** The ids of the rules a piece of source is refused by, in source order. */
async function refusedBy(source: string, filePath = "src/routes/Thing.tsx"): Promise<string[]> {
  const [result] = await eslint.lintText(`${source}\nexport {};\n`, { filePath });
  return (result?.messages ?? []).flatMap((message) => (message.ruleId ? [message.ruleId] : []));
}

describe("what the operator console may not do with a string", () => {
  it.each([
    ["eval", 'const run = (code: string) => eval(code);', "no-eval"],
    ["a function built from text", 'const run = new Function("return 1");', "no-new-func"],
    ["a timer given a string to evaluate", 'setTimeout("go()", 0);', "no-implied-eval"],
  ])("refuses %s", async (_name, source, rule) => {
    expect(await refusedBy(source)).toContain(rule);
  });

  it.each([
    ["dangerouslySetInnerHTML", 'const x = (html: string) => <div dangerouslySetInnerHTML={{ __html: html }} />;'],
  ])("refuses %s, and says why", async (_name, source) => {
    const [result] = await eslint.lintText(`${source}\nexport {};\n`, { filePath: "src/routes/Thing.tsx" });
    const refused = (result?.messages ?? []).filter((message) => message.ruleId === "no-restricted-syntax");
    expect(refused).toHaveLength(1);
    expect(refused[0]?.message).toContain("Render elements");
  });

  it.each([
    ["innerHTML", "const set = (el: HTMLElement, html: string) => { el.innerHTML = html; };"],
    ["outerHTML", "const set = (el: HTMLElement, html: string) => { el.outerHTML = html; };"],
    ["insertAdjacentHTML", 'const add = (el: HTMLElement, html: string) => el.insertAdjacentHTML("beforeend", html);'],
    ["document.write", "const put = (html: string) => document.write(html);"],
  ])("refuses %s", async (_name, source) => {
    expect(await refusedBy(source)).toContain("no-restricted-properties");
  });
});

describe("what the operator console may not ask the browser to do", () => {
  it.each([
    ["window.confirm", 'const ok = () => window.confirm("Suspend?");'],
    ["a bare confirm", 'const ok = () => confirm("Suspend?");'],
    ["alert", 'const tell = () => alert("Done");'],
    ["prompt", 'const ask = () => prompt("Reason?");'],
  ])("refuses %s: a confirmation is a dialog on the page, with a focus trap and a test", async (_name, source) => {
    expect(await refusedBy(source)).toContain("no-alert");
  });

  it.each(["log", "warn", "error", "info", "debug"])("refuses console.%s", async (method) => {
    expect(await refusedBy(`const say = () => console.${method}("x");`)).toContain("no-console");
  });
});

describe("what it leaves alone", () => {
  it("lets ordinary code through: text, and a method that happens to be named confirm", async () => {
    const source = [
      "export const dialog = { confirm() { return true; } };",
      "export const ok = () => dialog.confirm();",
      "export const shown = (el: HTMLElement) => el.textContent;",
    ].join("\n");
    expect(await refusedBy(source, "src/lib/thing.ts")).toEqual([]);
  });
});

describe("the token lint is still on beside the markup rule", () => {
  /** The message of every `no-restricted-syntax` refusal, so a test says *which* selector fired. */
  async function syntaxMessages(source: string, filePath: string): Promise<string[]> {
    const [result] = await eslint.lintText(`${source}\nexport {};\n`, { filePath });
    return (result?.messages ?? [])
      .filter((message) => message.ruleId === "no-restricted-syntax")
      .map((message) => message.message);
  }

  it("still refuses a raw colour in a source file, in the same file as the markup rule", async () => {
    const messages = await syntaxMessages('const accent = "#6c4cf1";', "src/routes/Thing.tsx");
    expect(messages).toHaveLength(1);
    expect(messages[0]).toContain("raw colour");
  });

  it("still refuses an arbitrary Tailwind value and an inline style that is not a custom property", async () => {
    const messages = await syntaxMessages(
      'const A = () => <div className="w-[13px]" style={{ width: 4 }} />;',
      "src/routes/Thing.tsx",
    );
    expect(messages.some((message) => message.includes("arbitrary value"))).toBe(true);
    expect(messages.some((message) => message.includes("inline style"))).toBe(true);
  });

  it("refuses a raw colour and dangerouslySetInnerHTML together, each with its own message", async () => {
    const messages = await syntaxMessages(
      'const A = () => <div className="bg-[#fff]" dangerouslySetInnerHTML={{ __html: "" }} />;',
      "src/routes/Thing.tsx",
    );
    expect(messages.some((message) => message.includes("Render elements"))).toBe(true);
    expect(messages.some((message) => message.includes("arbitrary value") || message.includes("raw colour"))).toBe(true);
  });

  it("does not hold a test to the token rules, which assert on classes and fixtures", async () => {
    expect(await syntaxMessages('const accent = "#6c4cf1";', "src/routes/Thing.test.tsx")).toEqual([]);
  });

  it("still holds a test to the markup rule", async () => {
    const messages = await syntaxMessages(
      'const A = () => <div dangerouslySetInnerHTML={{ __html: "" }} />;',
      "src/routes/Thing.test.tsx",
    );
    expect(messages).toHaveLength(1);
    expect(messages[0]).toContain("Render elements");
  });
});
