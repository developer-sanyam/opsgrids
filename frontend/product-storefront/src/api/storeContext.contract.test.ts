/**
 * The buyer's bootstrap types, checked against the schema the service actually publishes.
 *
 * Same chain as `serviceability.contract.test.ts`, for the same reason and after the same
 * incident: ADR-0010's erratum 5 records two units deriving two different shapes from prose while
 * the service shipped a third. ADR-0012 §11 prints its bodies *because* of that, and this test
 * exists so the printed body is never the thing this client is pinned to — only
 * `libs/contracts/openapi/storefront.json` is.
 *
 * - **TypeScript** proves witness ≡ interface (`satisfies Record<keyof T, true>` fails on a
 *   missing key and on a spare one).
 * - **This test** proves witness ≡ published schema.
 * - Therefore interface ≡ published schema.
 *
 * The check that earns this file its keep on its own is the last one: `tagline` and `logo_text`
 * are **required-but-nullable**. The keys are always present and may be `null`. Declaring them
 * optional under `exactOptionalPropertyTypes` makes "absent" and "null" two different types for
 * one wire state — ADR-0010's erratum 13 is this mistake made in the other direction.
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { PublicStoreContextIn, StoreContextOut } from "./storeContext";

const CONTRACT_RELATIVE = join("libs", "contracts", "openapi", "storefront.json");

/** The published schema, found by walking up from the working directory — see the sibling test. */
function findContract(): string {
  let directory = resolve(process.cwd());
  for (;;) {
    const candidate = join(directory, CONTRACT_RELATIVE);
    if (existsSync(candidate)) return candidate;
    const parent = dirname(directory);
    if (parent === directory) {
      throw new Error(`Could not find ${CONTRACT_RELATIVE} above ${process.cwd()}.`);
    }
    directory = parent;
  }
}

const CONTRACT_PATH = findContract();

interface JsonSchema {
  properties?: Record<string, { anyOf?: { type?: string }[]; type?: string }>;
  required?: string[];
  additionalProperties?: boolean;
}

interface OpenApiDocument {
  paths?: Record<string, Record<string, unknown>>;
  components?: { schemas?: Record<string, JsonSchema> };
}

const contract = JSON.parse(readFileSync(CONTRACT_PATH, "utf8")) as OpenApiDocument;

function component(name: string): JsonSchema {
  const schema = contract.components?.schemas?.[name];
  if (schema === undefined) {
    throw new Error(
      `${name} is not a component of ${CONTRACT_PATH}. The published schema is the contract — if the service renamed it, this client follows.`,
    );
  }
  return schema;
}

function propertyNames(name: string): string[] {
  return Object.keys(component(name).properties ?? {}).sort();
}

function requiredNames(name: string): string[] {
  return [...(component(name).required ?? [])].sort();
}

/** The `type`s a property accepts, whether stated directly or under a nullable `anyOf`. */
function acceptedTypes(name: string, property: string): string[] {
  const declared = (component(name).properties ?? {})[property];
  if (declared === undefined) throw new Error(`${name}.${property} is not in the published schema.`);
  const types = declared.anyOf?.map((option) => option.type ?? "") ?? [declared.type ?? ""];
  return [...types].sort();
}

/** `true` for every property the interface declares without `?`. */
type RequiredKeys<T> = {
  [K in keyof T]-?: undefined extends T[K] ? never : K;
}[keyof T];

/* --------------------------- the witnesses ---------------------------- */

const CONTEXT_FIELDS = {
  store: true,
  name: true,
  tagline: true,
  logo_text: true,
  currency: true,
  timezone: true,
  canonical_host: true,
  is_canonical: true,
  theme: true,
  checked_at: true,
} satisfies Record<keyof StoreContextOut, true>;

/**
 * Every field but `theme` is required. `theme` carries a Python-level default (`= None`) on
 * `StoreContextOut.theme`, so the published schema's own `required` list omits it even though
 * FastAPI's default response serialization always sends the key — `null` when no theme is
 * adopted, the document otherwise. The TS interface keeps it a required (non-optional) member for
 * that reason: every response genuinely carries the key, so a consumer should never need `?.` to
 * reach it.
 */
const CONTEXT_REQUIRED = {
  store: true,
  name: true,
  tagline: true,
  logo_text: true,
  currency: true,
  timezone: true,
  canonical_host: true,
  is_canonical: true,
  checked_at: true,
} satisfies Record<Exclude<RequiredKeys<StoreContextOut>, "theme">, true>;

const REQUEST_FIELDS = { host: true } satisfies Record<keyof PublicStoreContextIn, true>;
const REQUEST_REQUIRED = { host: true } satisfies Record<RequiredKeys<PublicStoreContextIn>, true>;

function keys(witness: Record<string, true>): string[] {
  return Object.keys(witness).sort();
}

/* ------------------------------ the checks ----------------------------- */

describe("StoreContextOut, against the published schema", () => {
  it("declares every property the schema declares, and no others", () => {
    expect(keys(CONTEXT_FIELDS)).toEqual(propertyNames("StoreContextOut"));
  });

  it("makes every field but theme required — every key but that one has no Python-side default", () => {
    /*
     * `theme` is the one exception: `StoreContextOut.theme` defaults to `None` in
     * `schemas.py`, so the schema's own `required` list omits it even though FastAPI's response
     * serialization sends the key on every response regardless (`null` with no theme adopted).
     * The interface keeps it non-optional for that reason — see the comment on `CONTEXT_REQUIRED`.
     */
    expect(keys(CONTEXT_REQUIRED)).toEqual(requiredNames("StoreContextOut"));
  });

  it("has tagline and logo_text required AND nullable — not optional", () => {
    /*
     * The distinction this whole file exists for. `required` carries both names, and both accept
     * `null`, so the wire has one state for "the merchant has not written one". A `?:` here would
     * invent a second, and under `exactOptionalPropertyTypes` the two compare differently — the
     * mistake ADR-0010's erratum 13 records, made the other way round.
     */
    expect(requiredNames("StoreContextOut")).toContain("tagline");
    expect(requiredNames("StoreContextOut")).toContain("logo_text");
    expect(acceptedTypes("StoreContextOut", "tagline")).toEqual(["null", "string"]);
    expect(acceptedTypes("StoreContextOut", "logo_text")).toEqual(["null", "string"]);
  });

  it("does not make name, canonical_host or is_canonical nullable", () => {
    // The renderer reads all three without a `??`, so a schema change that made one nullable has
    // to fail here rather than on a shop front.
    expect(acceptedTypes("StoreContextOut", "name")).toEqual(["string"]);
    expect(acceptedTypes("StoreContextOut", "canonical_host")).toEqual(["string"]);
    expect(acceptedTypes("StoreContextOut", "is_canonical")).toEqual(["boolean"]);
  });
});

describe("PublicStoreContextIn, against the published schema", () => {
  it("carries host and nothing else", () => {
    expect(keys(REQUEST_FIELDS)).toEqual(propertyNames("PublicStoreContextIn"));
    expect(keys(REQUEST_REQUIRED)).toEqual(requiredNames("PublicStoreContextIn"));
  });

  it("forbids additional properties, so the request body may carry no extras", () => {
    // `additionalProperties: false` is why the bundle sends `{ host }` and never adds a slug, a
    // path or a build-time constant alongside it.
    expect(component("PublicStoreContextIn").additionalProperties).toBe(false);
  });
});

describe("the bootstrap route", () => {
  it("is a POST at the exact path the bundle calls", () => {
    /* `GatewayPolicy.public_paths` is an exact-path frozenset, so this string is the contract as
       much as the body is — a prefix or a path parameter is not expressible at all. */
    const route = contract.paths?.["/api/v1/storefront/public/store-context"];
    expect(route).toBeDefined();
    expect(Object.keys(route ?? {})).toEqual(["post"]);
  });

  it("documents 404 and 503 as different facts", () => {
    // The distinction the whole buyer surface branches on. If the service ever collapses them,
    // this fails here rather than by showing "no shop here" to a shop's customers.
    const responses = (contract.paths?.["/api/v1/storefront/public/store-context"]?.post as {
      responses?: Record<string, unknown>;
    })?.responses;
    expect(Object.keys(responses ?? {}).sort()).toEqual(["200", "404", "422", "429", "503"]);
  });
});
