/**
 * The buyer's wire type, checked against the schema the service actually publishes.
 *
 * **This test exists because its absence cost a release.** `ServiceabilityOut` was first written
 * from ADR-0010's prose — the ADR describes the answer and prints no response body — and came out
 * with an `outcome: "SERVED" | "UNSERVED"` field the service has never sent, a top-level
 * `promise_date` that is really `promise.date`, and a top-level `currency` that is really
 * `fees.currency`. Every unit test passed: they were all written against the same guess.
 *
 * So the one thing this file must not do is restate the hand-written type. It reads
 * `libs/contracts/openapi/storefront.json` off disk — the generated artefact, the same file the
 * merchant's admin transcribed — and compares it against a witness object whose keys the compiler
 * forces to equal the interface's. That is what makes the chain hold end to end:
 *
 * - **TypeScript** proves witness ≡ interface (`satisfies Record<keyof T, true>` fails on a key
 *   that is missing and on one that is spare).
 * - **This test** proves witness ≡ published schema.
 * - Therefore interface ≡ published schema, and neither half can move without the other.
 *
 * A fourth `reason`, a renamed field or a newly required one fails here, at `pnpm test`, and not
 * in front of a buyer being told the shop does not deliver to a postal code it delivers to.
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type {
  FeesOut,
  PromiseKind,
  PromiseOut,
  ServiceabilityOut,
  UnservedReason,
  ZoneMode,
} from "./client";

const CONTRACT_RELATIVE = join("libs", "contracts", "openapi", "storefront.json");

/**
 * The published schema, found by walking up from the working directory.
 *
 * A search rather than `../../../..`: the suite is transformed and runs in jsdom, where
 * `import.meta.url` is not a `file:` URL, and a fixed hop count would also break the day this app
 * moves a directory — which is the wrong thing for a contract test to be fragile about. Failing to
 * find it throws, because a contract test that cannot find the contract must not pass.
 */
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
  properties?: Record<string, unknown>;
  required?: string[];
}

interface OpenApiDocument {
  components?: { schemas?: Record<string, JsonSchema> };
}

/* Read once, and let a missing or unreadable file throw: a contract test that silently skips when
   it cannot find the contract is the same as no contract test. */
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

/** The enum a property declares, whether it is stated directly or under a nullable `anyOf`. */
function enumValues(name: string, property: string): string[] {
  const properties = component(name).properties ?? {};
  const declared = properties[property] as
    | { enum?: string[]; anyOf?: { enum?: string[] }[] }
    | undefined;
  const values = declared?.enum ?? (declared?.anyOf ?? []).flatMap((option) => option.enum ?? []);
  if (values.length === 0) {
    throw new Error(`${name}.${property} declares no enum in the published schema.`);
  }
  return [...values].sort();
}

/**
 * `true` for every property the interface declares without `?`.
 *
 * Indexed access widens an optional property with `undefined` under `exactOptionalPropertyTypes`,
 * which is exactly the distinction the schema's `required` list makes.
 */
type RequiredKeys<T> = {
  [K in keyof T]-?: undefined extends T[K] ? never : K;
}[keyof T];

/* --------------------------- the witnesses ---------------------------- */
/* Each `satisfies` is the compile-time half of the check. Add a field to the interface and this
   file stops compiling until the field is named here; name one that is not on the interface and it
   stops compiling too. Neither can be satisfied by editing this file alone — the assertions below
   then have to agree with the schema on disk. */

const SERVICEABILITY_FIELDS = {
  postal_code: true,
  serviceable: true,
  mode: true,
  reason: true,
  promise: true,
  fees: true,
  buyer_note: true,
  checked_at: true,
} satisfies Record<keyof ServiceabilityOut, true>;

const SERVICEABILITY_REQUIRED = {
  postal_code: true,
  serviceable: true,
  mode: true,
  checked_at: true,
} satisfies Record<RequiredKeys<ServiceabilityOut>, true>;

const PROMISE_FIELDS = {
  kind: true,
  minutes: true,
  date: true,
} satisfies Record<keyof PromiseOut, true>;

const PROMISE_REQUIRED = { kind: true } satisfies Record<RequiredKeys<PromiseOut>, true>;

const FEES_FIELDS = {
  currency: true,
  delivery_fee_minor: true,
  free_delivery_above_minor: true,
  min_order_minor: true,
  display_only: true,
} satisfies Record<keyof FeesOut, true>;

const FEES_REQUIRED = {
  currency: true,
  delivery_fee_minor: true,
} satisfies Record<RequiredKeys<FeesOut>, true>;

const MODES = {
  PROMISED: true,
  SLOTTED: true,
  UNSERVED: true,
} satisfies Record<ZoneMode, true>;

const REASONS = {
  no_mapping: true,
  excluded: true,
  zone_inactive: true,
} satisfies Record<UnservedReason, true>;

const PROMISE_KINDS = {
  DURATION: true,
  DATE_OFFSET: true,
} satisfies Record<PromiseKind, true>;

function keys(witness: Record<string, true>): string[] {
  return Object.keys(witness).sort();
}

/* ------------------------------ the checks ----------------------------- */

describe("ServiceabilityOut, against the published schema", () => {
  it("declares every property the schema declares, and no others", () => {
    expect(keys(SERVICEABILITY_FIELDS)).toEqual(propertyNames("ServiceabilityOut"));
  });

  it("makes exactly the schema's four fields required", () => {
    // `reason`, `promise`, `fees` and `buyer_note` are absent from `required`, so they are optional
    // here too and every read of them goes through a `??`. A body that omits one is an ordinary
    // answer, not a fault, and must not throw in front of a buyer.
    expect(keys(SERVICEABILITY_REQUIRED)).toEqual(requiredNames("ServiceabilityOut"));
  });

  it("carries none of the three fields the buyer's half once guessed at", () => {
    /*
     * The regression, named. Each of these was declared, rendered and unit-tested against a server
     * that has never sent it: `outcome` was the classifier, so every answer fell through to
     * "unknown"; `promise_date` and `currency` are one level down, under `promise` and `fees`.
     */
    const declared = propertyNames("ServiceabilityOut");
    expect(declared).not.toContain("outcome");
    expect(declared).not.toContain("promise_date");
    expect(declared).not.toContain("currency");
  });

  it("agrees with the schema on which modes and which refusals exist", () => {
    // `mode` is required and never null — `UNSERVED` is a value it takes, not an absence. That is
    // the whole reason `serviceable` is what the client classifies on.
    expect(keys(MODES)).toEqual(enumValues("ServiceabilityOut", "mode"));
    expect(keys(REASONS)).toEqual(enumValues("ServiceabilityOut", "reason"));
    expect(requiredNames("ServiceabilityOut")).toContain("mode");
  });
});

describe("the nested objects, against the published schema", () => {
  it("transcribes PromiseOut — the promise is an object, not three flat fields", () => {
    expect(keys(PROMISE_FIELDS)).toEqual(propertyNames("PromiseOut"));
    expect(keys(PROMISE_REQUIRED)).toEqual(requiredNames("PromiseOut"));
    expect(keys(PROMISE_KINDS)).toEqual(enumValues("PromiseOut", "kind"));
  });

  it("transcribes FeesOut — the currency belongs to the fees, not to the answer", () => {
    expect(keys(FEES_FIELDS)).toEqual(propertyNames("FeesOut"));
    expect(keys(FEES_REQUIRED)).toEqual(requiredNames("FeesOut"));
  });
});

describe("the request body", () => {
  it("sends the store slug in the body, because the gateway's public paths are exact matches", () => {
    // ADR-0010 §4: `GatewayPolicy.public_paths` is an exact-path frozenset, so `/public/store/{slug}`
    // is not expressible. A refactor that moved the slug into the path would 401 every buyer.
    expect(requiredNames("PublicServiceabilityIn")).toEqual(["postal_code", "store"]);
  });
});
