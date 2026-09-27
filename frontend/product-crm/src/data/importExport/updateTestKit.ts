/**
 * Test-only helpers for the entities' `update` specs (ADR-0022, addendum). The rules of matching and
 * diffing are the engine's (`matchRow`, `buildUpdate`, `planUpdate` in `@opsgrids/client`, tested
 * there); what is under test here is what each CRM list tells them: which columns change, how a
 * cell becomes the stored terms, and the exact requests a row sends. A spec is run through the
 * engine's own `planUpdate`, and its requests are recorded by the fake gateway in `entityTestKit`.
 */
import { planUpdate } from "@opsgrids/client";
import type { ImportContext, ImportEntity, ParsedValue, UpdatePlan, UpdateSpec } from "@opsgrids/client";

import { parsedRow, present } from "./entityTestKit";
import type { Recorded } from "./entityTestKit";

/** The entity's update spec, which a test needs to be there. */
export function updateSpecOf<Payload, Item, Filters>(entity: ImportEntity<Payload, Item, Filters>): UpdateSpec<Item> {
  return present(entity.update, `${entity.id} update`);
}

/**
 * What a sheet row would do to a stored record: the engine's plan for it, or null when it changes
 * nothing (and so makes no request). `cells` are the coerced cells (an enum's value, money in
 * minor units, blank as absent); `cleared` names the columns whose cell said `[clear]`. A refused
 * row throws the `RowProblem` the engine would report.
 */
export function planFor<Payload, Item, Filters>(
  entity: ImportEntity<Payload, Item, Filters>,
  existing: Item,
  cells: Readonly<Record<string, ParsedValue>>,
  ctx: ImportContext,
  cleared: readonly string[] = [],
): UpdatePlan | null {
  return planUpdate(entity.columns, updateSpecOf(entity), parsedRow(entity.columns, cells), new Set(cleared), existing, ctx);
}

/** Runs a plan the test expects to exist. */
export async function runPlan(plan: UpdatePlan | null): Promise<void> {
  await present(plan ?? undefined, "an update plan").run();
}

/** The requests a run sent, as `PATCH /api/v1/customers/comp-1`, in order. */
export function requestLines(calls: readonly Recorded[]): string[] {
  return calls.map((call) => `${call.method} ${call.url.pathname}`);
}

/** A body's values, to say that none is null (the services answer a null with a 409 or a 500). */
export function valuesOf(body: unknown): unknown[] {
  return typeof body === "object" && body !== null ? Object.values(body) : [];
}
