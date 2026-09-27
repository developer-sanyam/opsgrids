/**
 * Test support for the `update` specs: the engine's own pure rules (`planUpdate`, `matchRow`,
 * `indexExisting`) applied to a spec the way the dialog will apply them, and a run of the resulting
 * plan against the stubbed gateway so a test asserts the exact request the server would receive.
 * What the engine does around them (parsing a sheet, reading the whole list, reporting a row) is
 * not repeated here; a test hands over the parsed row the engine would.
 */
import { indexExisting, matchRow, planUpdate } from "@opsgrids/client";
import type { ImportContext, ImportEntity, ParsedRow, UpdatePlan, UpdateSpec } from "@opsgrids/client";

import { stubGateway } from "./entityTestKit";

export function updateSpec<Payload, Item, Filters>(entity: ImportEntity<Payload, Item, Filters>): UpdateSpec<Item> {
  if (entity.update === undefined) throw new Error(`${entity.id} has no update spec`);
  return entity.update;
}

/**
 * What the engine does with a row that matched `existing`: the plan, or null when nothing changes.
 * `cleared` lists the columns whose cell said `[clear]` (the engine has already emptied their value).
 */
export function planFor<Payload, Item, Filters>(
  entity: ImportEntity<Payload, Item, Filters>,
  existing: Item,
  row: ParsedRow,
  ctx: ImportContext,
  cleared: readonly string[] = [],
): UpdatePlan | null {
  return planUpdate(entity.columns, updateSpec(entity), row, new Set(cleared), existing, ctx);
}

/** The record a row names among `stored`, by its Record ID or by the list's unique key. */
export function matchAmong<Payload, Item, Filters>(
  entity: ImportEntity<Payload, Item, Filters>,
  stored: readonly Item[],
  recordId: string | null,
  key: string | null,
) {
  return matchRow(indexExisting(updateSpec(entity), stored), recordId, key);
}

export interface SentRequest {
  method: string;
  path: string;
  body: unknown;
}

/** A plan a test expects to exist, so its `run` can be called without a non-null assertion. */
export function expectPlan(plan: UpdatePlan | null): UpdatePlan {
  if (plan === null) throw new Error("the row changes nothing, but the test expected it to change something");
  return plan;
}

/** Runs a plan against a gateway that accepts everything, and returns the requests it made. */
export async function requestsOf(plan: UpdatePlan | null): Promise<SentRequest[]> {
  const recorded = stubGateway(() => ({ body: {} }));
  await plan?.run();
  return recorded.map(({ url, init }) => ({
    method: init.method ?? "GET",
    path: url.pathname,
    body: typeof init.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
  }));
}

/**
 * Runs a plan the way the runner does, handing each of its steps to a pacing function, and counts
 * the steps and the HTTP requests. The runner retries per step after a 429, so a step that made two
 * requests would repeat the first when the second was refused.
 */
export async function stepsAndRequests(plan: UpdatePlan | null): Promise<{ steps: number; requests: number }> {
  const recorded = stubGateway(() => ({ body: {} }));
  let steps = 0;
  await plan?.run(async (step) => {
    steps += 1;
    await step();
  });
  return { steps, requests: recorded.length };
}

/** The one request a plan makes, for a test that is about its body. */
export async function soleRequest(plan: UpdatePlan | null): Promise<SentRequest> {
  const requests = await requestsOf(plan);
  const [only] = requests;
  if (requests.length !== 1 || only === undefined) {
    throw new Error(`expected exactly one request, got ${requests.length}`);
  }
  return only;
}

/** True when a value, at any depth of a request body, is `null`. */
export function containsNull(value: unknown): boolean {
  if (value === null) return true;
  if (typeof value !== "object") return false;
  return Object.values(value).some(containsNull);
}
