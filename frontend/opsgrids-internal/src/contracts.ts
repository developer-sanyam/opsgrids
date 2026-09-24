/**
 * The committed OpenAPI documents (`libs/contracts/openapi/*.json`), read for the suites that hold
 * the console's hand-written wire types to them. Imported by tests only.
 *
 * There is no generated client in this repository: each app writes its own request and response
 * types. What keeps them honest is a test that reads the document the backend actually publishes
 * and fails when the two disagree — so a renamed field breaks a test, not a sign-in.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export interface SchemaObject {
  type?: string;
  const?: unknown;
  enum?: unknown[];
  minimum?: number;
  properties?: Record<string, SchemaObject>;
  required?: string[];
  $ref?: string;
  anyOf?: SchemaObject[];
  oneOf?: SchemaObject[];
  items?: SchemaObject;
}

interface Operation {
  summary?: string;
  parameters?: { name: string; in: string; required?: boolean; schema: SchemaObject }[];
  requestBody?: { content: Record<string, { schema: SchemaObject }> };
  responses: Record<string, { content?: Record<string, { schema: SchemaObject }> }>;
}

export interface OpenApiDocument {
  paths: Record<string, Record<string, Operation>>;
  components: { schemas: Record<string, SchemaObject> };
}

const cache = new Map<string, OpenApiDocument>();

/** `libs/contracts/openapi/<service>.json`, relative to `frontend/opsgrids-internal/src/`. */
export function openApi(service: string): OpenApiDocument {
  const cached = cache.get(service);
  if (cached) return cached;
  // Not `new URL(…, import.meta.url)`: Vite reads that shape as an asset import and refuses a file
  // outside the package, which is precisely where the contracts live.
  const here = dirname(fileURLToPath(import.meta.url));
  const file = resolve(here, "../../../libs/contracts/openapi", `${service}.json`);
  const document = JSON.parse(readFileSync(file, "utf-8")) as OpenApiDocument;
  cache.set(service, document);
  return document;
}

export function schemaOf(document: OpenApiDocument, name: string): SchemaObject {
  const schema = document.components.schemas[name];
  if (schema === undefined) throw new Error(`The contract has no schema named ${name}`);
  return schema;
}

/** The operation for `method` on `path` (the `/api/v1` prefix included), or a loud failure. */
export function operationOf(document: OpenApiDocument, method: string, path: string): Operation {
  const operation = document.paths[path]?.[method.toLowerCase()];
  if (operation === undefined) throw new Error(`The contract has no ${method} ${path}`);
  return operation;
}

/** Whether the contract publishes `method` on `path`, without failing when it does not. */
export function hasOperation(document: OpenApiDocument, method: string, path: string): boolean {
  return document.paths[path]?.[method.toLowerCase()] !== undefined;
}

/** The `#/components/schemas/<name>` a `$ref` points at, or `null` for anything else. */
export function refName(schema: SchemaObject | undefined): string | null {
  const ref = schema?.$ref;
  return ref?.startsWith("#/components/schemas/") ? ref.slice("#/components/schemas/".length) : null;
}
