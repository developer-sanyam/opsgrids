/**
 * What a live answer must look like before a screen is handed it.
 *
 * The console's view models were designed ahead of the operator API, and `api/contract.test.ts`
 * lists, with the OpenAPI document beside each line, the places the two still differ. Until they
 * are reconciled, a live read can come back as valid JSON of the wrong shape — `organizations`
 * where the screen wants `items` — and a screen given that does not fail with a message, it throws
 * inside a render and leaves a blank page. So a read is checked at the door, and a mismatch is
 * named as one: an error that says the console and the API are out of step, which no retry fixes.
 *
 * This checks that the members a screen reads *exist*. It is not a validator and does not pretend
 * to be one.
 */

export class ContractMismatchError extends Error {
  /** The route that answered. */
  readonly path: string;
  /** The members the console reads that the answer does not carry. */
  readonly missing: readonly string[];

  constructor(path: string, missing: readonly string[]) {
    super(
      `${path} answered in a shape this console does not read yet — it has no ${missing
        .map((member) => `\`${member}\``)
        .join(", ")}. The console and the operator API are out of step.`,
    );
    this.name = "ContractMismatchError";
    this.path = path;
    this.missing = missing;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** `data` as `T`, provided it is an object carrying every named member; otherwise a mismatch. */
export function expectMembers<T>(path: string, data: unknown, members: readonly string[]): T {
  if (!isRecord(data)) throw new ContractMismatchError(path, members);
  const missing = members.filter((member) => !(member in data));
  if (missing.length > 0) throw new ContractMismatchError(path, missing);
  return data as T;
}

/** A `{items, total}` page whose `items` is really a list. */
export function expectPage<T>(path: string, data: unknown): { items: T[]; total: number } {
  const page = expectMembers<{ items: unknown; total: unknown }>(path, data, ["items", "total"]);
  if (!Array.isArray(page.items)) throw new ContractMismatchError(path, ["items (a list)"]);
  return page as { items: T[]; total: number };
}
