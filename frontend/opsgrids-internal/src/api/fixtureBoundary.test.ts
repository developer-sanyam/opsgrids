/**
 * The fixtures — fabricated organizations, contacts, invoices — must not ship in the bundle an
 * operator downloads. They stay out because `client.ts` reaches them only through a dynamic
 * `import()` behind `USE_FIXTURES`, which is `false` in a production build; one static import from
 * anywhere in the app and they are back, with nothing failing to say so.
 *
 * A build is too slow to run in a unit suite, so this holds the cause instead of the symptom: no
 * source file outside the fixtures and their tests imports them statically. (The build's own
 * result — no fixture string in `dist/` — was checked when this was written and is in the slice
 * report.)
 */

import { describe, expect, it } from "vitest";

const sources = import.meta.glob<string>(["/src/**/*.{ts,tsx}", "!/src/**/*.test.{ts,tsx}"], {
  query: "?raw",
  import: "default",
  eager: true,
});

/** Files allowed to name the fixtures: the fixtures themselves, the one module that serves them. */
function mayImportFixtures(path: string): boolean {
  return path.startsWith("/src/api/fixtures/") || path === "/src/api/fixtureClient.ts";
}

/** A static import or re-export of the fixtures, in any of the ways a module can be named. */
const STATIC_FIXTURE_IMPORT = /^\s*(?:import|export)\b[^;]*?\bfrom\s+["'](?:@\/api\/fixtures|\.{1,2}\/(?:api\/)?fixtures|\.{1,2}\/fixtureClient|@\/api\/fixtureClient)[^"']*["']/m;

describe("the fixtures stay out of a production bundle", () => {
  it("finds the sources it is meant to police", () => {
    expect(Object.keys(sources)).toEqual(expect.arrayContaining(["/src/api/client.ts", "/src/router.tsx"]));
  });

  it("no module outside the fixtures imports them statically", () => {
    const offenders = Object.entries(sources)
      .filter(([path]) => !mayImportFixtures(path))
      .filter(([, text]) => STATIC_FIXTURE_IMPORT.test(text))
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });

  it("the one door is a dynamic import in client.ts", () => {
    const client = sources["/src/api/client.ts"] ?? "";
    expect(client).toMatch(/import\(["']\.\/fixtureClient["']\)/);
  });

  it("the check would catch a static import (so a green result means something)", () => {
    expect(STATIC_FIXTURE_IMPORT.test(`import { ORGANIZATIONS } from "./fixtures";`)).toBe(true);
    expect(STATIC_FIXTURE_IMPORT.test(`import { x } from "@/api/fixtures";`)).toBe(true);
    expect(STATIC_FIXTURE_IMPORT.test(`export * from "./fixtures/orgs";`)).toBe(true);
    expect(STATIC_FIXTURE_IMPORT.test(`import { overview } from "./fixtureClient";`)).toBe(true);
    expect(STATIC_FIXTURE_IMPORT.test(`const fixtures = () => import("./fixtureClient");`)).toBe(false);
  });
});
