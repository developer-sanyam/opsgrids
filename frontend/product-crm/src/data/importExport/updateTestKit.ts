/**
 * Test-only helpers for the entities' `update` specs (ADR-0022, addendum). The rules of matching and
 * diffing are the engine's (`matchRow`, `buildUpdate`, `planUpdate` in `@opsgrids/client`, tested
 * there); what is under test here is what each CRM list tells them: which columns change, how a
 * cell becomes the stored terms, and the exact requests a row sends. A spec is run through the
 * engine's own `planUpdate`, and its requests are recorded by the fake gateway in `entityTestKit`.
 */
import { makeLookup, planUpdate } from "@opsgrids/client";
import type { ImportContext, ImportEntity, LookupSet, ParsedValue, UpdatePlan, UpdateSpec } from "@opsgrids/client";

import { parsedRow, present } from "./entityTestKit";
import type { Recorded } from "./entityTestKit";

const ACTIVE_REPS = [
  { label: "sofia@example.com", value: "own-1" },
  { label: "Grace@Example.com", value: "own-2" },
  // Two active reps under one address: the name cannot say which.
  { label: "twin@example.com", value: "own-7" },
  { label: "Twin@example.com", value: "own-8" },
];

/**
 * The reps as `loadOwnerLookups` builds them: `owners` holds the active ones (what the column's
 * dropdown offers), `ownersAll` every one, including a rep who has left (`own-3`), because a record
 * keeps its owner after they go and an export writes them by email.
 */
export const OWNER_LOOKUPS: LookupSet = {
  owners: makeLookup(ACTIVE_REPS),
  ownersAll: makeLookup([...ACTIVE_REPS, { label: "former@example.com", value: "own-3" }]),
};

/** Written out in full, so a change to the sentence a user reads is a change to a test. */
const OWNER_HINT_UPDATE =
  "Use the email of an active sales rep, see the Lists sheet. Leave blank to keep the current owner.";
export const notAnAcceptedOwner = (email: string): string =>
  `'${email}' is not one of the accepted values for Owner email (see the Lists sheet). ${OWNER_HINT_UPDATE}`;
export const ambiguousOwner = (email: string): string =>
  `'${email}' matches more than one of the accepted values for Owner email, so it cannot be chosen. ${OWNER_HINT_UPDATE}`;

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
