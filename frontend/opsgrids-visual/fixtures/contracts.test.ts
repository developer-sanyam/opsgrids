/**
 * Every mocked response is one the real service could send.
 *
 * Each route in `fixtures/api/routes.ts` names an OpenAPI operation in `libs/contracts/openapi/`
 * — the committed contracts `make openapi-check` keeps equal to the services' own code. Here the
 * fixture is validated against that operation's response schema, the route's path against the
 * operation's path template, and its query keys against the operation's declared parameters. When
 * a service changes its shape, this fails before a screenshot built on the old shape can pass.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { Ajv2020 } from "ajv/dist/2020.js";
import { describe, expect, it } from "vitest";

import { FIXTURE_ROOT, readFixture } from "../lib/api";
import type { MockRoute } from "../lib/api";
import { ALL_ROUTES } from "./api/routes";

const CONTRACTS = fileURLToPath(new URL("../../../libs/contracts/openapi/", import.meta.url));

interface Operation {
  parameters?: { name: string; in: string }[];
  responses: Record<string, { content?: Record<string, { schema?: unknown }> }>;
}

type OpenApi = { paths: Record<string, Record<string, Operation>> };

const specs = new Map<string, OpenApi>();
// `strict: false`: an OpenAPI document carries keywords JSON Schema does not know (`openapi`,
// `paths`, `discriminator`). Formats are not asserted — the contracts use them as documentation.
const ajv = new Ajv2020({ strict: false, validateSchema: false, validateFormats: false, allErrors: true });

function spec(name: string): OpenApi {
  let doc = specs.get(name);
  if (!doc) {
    doc = JSON.parse(readFileSync(join(CONTRACTS, `${name}.json`), "utf8")) as OpenApi;
    specs.set(name, doc);
    ajv.addSchema(doc, `${name}.json`);
  }
  return doc;
}

function pointer(...segments: string[]): string {
  return segments.map((segment) => segment.replace(/~/g, "~0").replace(/\//g, "~1")).join("/");
}

function operation(route: MockRoute): Operation | undefined {
  const method = route.contract.method ?? "get";
  return spec(route.contract.spec).paths[route.contract.path]?.[method];
}

function templateMatches(template: string, path: string): boolean {
  const pattern = template.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\\\{[^}]+\\\}/g, "[^/]+");
  return new RegExp(`^${pattern}$`).test(path);
}

const routes = Object.entries(ALL_ROUTES).flatMap(([app, list]) => list.map((route) => ({ app, route })));

describe("API fixtures", () => {
  it.each(routes)("$app $route.method $route.path → $route.fixture matches its contract", ({ route }) => {
    const op = operation(route);
    expect(op, `${route.contract.spec}.json has no ${route.contract.method ?? "get"} ${route.contract.path}`).toBeDefined();
    expect(templateMatches(route.contract.path, route.path)).toBe(true);

    const declared = new Set((op?.parameters ?? []).filter((p) => p.in === "query").map((p) => p.name));
    expect(Object.keys(route.query ?? {}).filter((key) => !declared.has(key))).toEqual([]);

    const status = route.contract.status ?? String(route.status ?? 200);
    const ref = `${route.contract.spec}.json#/${pointer(
      "paths",
      route.contract.path,
      route.contract.method ?? "get",
      "responses",
      status,
      "content",
      "application/json",
      "schema",
    )}`;
    const validate = ajv.compile({ $ref: ref });
    const valid = validate(readFixture(route.fixture));
    expect(valid ? [] : validate.errors).toEqual([]);
  });

  it("has no fixture that no route serves", () => {
    function jsonUnder(dir: string): string[] {
      return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
        entry.isDirectory()
          ? jsonUnder(join(dir, entry.name))
          : entry.name.endsWith(".json")
            ? [relative(FIXTURE_ROOT, join(dir, entry.name))]
            : [],
      );
    }
    const served = new Set(routes.map(({ route }) => route.fixture));
    expect(jsonUnder(FIXTURE_ROOT).filter((file) => !served.has(file))).toEqual([]);
  });
});
