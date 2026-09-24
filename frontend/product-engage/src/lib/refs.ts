import { ApiError } from "@opsgrids/client";

import { FULL_PAGE, getList, listDomains, listLists, listSenders } from "@/api/client";
import type { CampaignOut, ContactListOut, DomainOut, Page, SenderOut } from "@/api/types";

/**
 * The reference data a campaign screen names things with — senders, their domains, and lists — and
 * the three honest ways reading it can fall short.
 *
 * **It can be incomplete.** The service serves at most `FULL_PAGE` rows a page, and an organization
 * may have more lists than that. Every page is read, up to `MAX_PAGES`; past that the picker is a
 * *partial* view and says so ("showing 1,000 of 2,500") rather than pretending the option it lacks
 * does not exist. What a campaign already points at is never subject to that cap: its list is read
 * by id (`resolveLists`), so a draft addressed to list #2,301 opens on that list and not on
 * whichever option happens to sort first.
 *
 * **It can be refused.** A custom role may hold the campaign permissions and not the list or
 * sender reads. Each read settles on its own (`settle`), so a refused one costs the screen that
 * one name — never the rows it could otherwise show.
 *
 * **It can be genuinely gone.** Only a real `404` on the by-id read says a list no longer exists.
 * A name that simply was not fetched is left unnamed, and every label built from it says "a list" —
 * never "could not be found", which is a claim about the service and not about this app's page size.
 *
 * **It costs calls.** See the budget below.
 */

/** Pages read for a picker: `MAX_PAGES` × `FULL_PAGE` rows before it becomes a partial view. */
export const MAX_PAGES = 5;

/**
 * The call budget. The gateway allows 300 requests a minute a user, and a screen that reads
 * reference data is spending it on the person's behalf — so a read is as few calls as it can be
 * (`loadRefs` is one call each for an organization with under 200 senders, domains and lists), no
 * more than `PAGES_IN_FLIGHT` pages are ever outstanding, and a screen reads *only* what it shows:
 * a campaign list reads the lists on its own page by id (`resolveLists`, at most `BY_ID_IN_FLIGHT`
 * at once) and never pages through every list to find them.
 */
const PAGES_IN_FLIGHT = 2;
const BY_ID_IN_FLIGHT = 4;

export interface Loaded<T> {
  items: T[];
  /** The service's own count — how many exist, not how many were read. */
  total: number;
}

/** `run` over `items` with at most `limit` in flight, results in the order of `items`. */
async function mapLimited<T, R>(
  items: readonly T[],
  limit: number,
  run: (item: T) => Promise<R>,
): Promise<R[]> {
  const results: R[] = new Array<R>(items.length);
  const queue = items.entries();
  async function worker(): Promise<void> {
    for (const [index, item] of queue) results[index] = await run(item);
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

/**
 * Every page, up to `MAX_PAGES`, each row once. Rows are de-duplicated by id because these are
 * `OFFSET` pages: a row written between two requests can shift a neighbour across a page boundary,
 * and a picker that listed it twice would offer the same sender twice.
 */
async function readEveryPage<T extends { id: string }>(
  read: (page: number) => Promise<Page<T>>,
): Promise<Loaded<T>> {
  const first = await read(1);
  const wanted = Math.min(first.pages, MAX_PAGES);
  const laterPages = Array.from({ length: Math.max(wanted - 1, 0) }, (_, index) => index + 2);
  const later = await mapLimited(laterPages, PAGES_IN_FLIGHT, read);
  const seen = new Set<string>();
  const items: T[] = [];
  for (const page of [first, ...later]) {
    for (const item of page.items) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      items.push(item);
    }
  }
  return { items, total: first.total };
}

/** One reference read, settled: what came back, or the error for why it did not. */
export interface Picker<T> {
  items: T[];
  /** How many of `items` came from the paged read (a list read by id is on top of these). */
  loaded: number;
  total: number;
  /** Why the read was refused or failed. `items` is then empty. Kept as the error itself rather
   *  than a sentence so a `402` can still be told from a `403`. */
  error: Error | null;
}

export function partial<T>(picker: Picker<T>): boolean {
  return picker.error === null && picker.total > picker.loaded;
}

/** The service's own sentence for a refused read, or the one thing this app can say for a read
 *  that never got an answer. */
export function failureText(error: Error): string {
  return error instanceof ApiError ? error.message : "Couldn't reach the server.";
}

/**
 * Whether a read was refused for *who is asking* rather than for what was asked — a `401`, a `402`
 * (product not active) or a `403`. Another read that needs the same permission will be refused the
 * same way, so there is nothing to learn by making it.
 */
export function isDenied(error: Error | null): boolean {
  return (
    error instanceof ApiError &&
    (error.status === 401 || error.status === 402 || error.status === 403)
  );
}

async function settle<T>(read: () => Promise<Loaded<T>>): Promise<Picker<T>> {
  try {
    const { items, total } = await read();
    return { items, loaded: items.length, total, error: null };
  } catch (caught) {
    return {
      items: [],
      loaded: 0,
      total: 0,
      error: caught instanceof Error ? caught : new Error(String(caught)),
    };
  }
}

export interface Refs {
  senders: Picker<SenderOut>;
  domains: Picker<DomainOut>;
  lists: Picker<ContactListOut>;
}

/** A picker for a read that was never made: nothing loaded, nothing wrong. */
export function notRead<T>(): Picker<T> {
  return { items: [], loaded: 0, total: 0, error: null };
}

export function loadSenders(): Promise<Picker<SenderOut>> {
  return settle(() => readEveryPage((page) => listSenders({ page })));
}

export function loadDomains(): Promise<Picker<DomainOut>> {
  return settle(() => readEveryPage((page) => listDomains({ page })));
}

export function loadLists(): Promise<Picker<ContactListOut>> {
  return settle(() => readEveryPage((page) => listLists({ page, size: FULL_PAGE })));
}

/** What a draft's form needs: the three pickers, in parallel. One call each for a typical
 *  organization. */
export async function loadRefs(): Promise<Refs> {
  const [senders, domains, lists] = await Promise.all([loadSenders(), loadDomains(), loadLists()]);
  return { senders, domains, lists };
}

export interface ResolvedLists {
  /** Every list this screen can name: the paged read, plus any read by id. */
  lists: ContactListOut[];
  /** Ids the service answered `404` for — the only lists that can be said to no longer exist. */
  gone: ReadonlySet<string>;
}

type ListAnswer =
  | { kind: "found"; list: ContactListOut }
  | { kind: "gone"; id: string }
  | { kind: "unnamed" }
  | { kind: "denied" };

async function readListById(id: string): Promise<ListAnswer> {
  try {
    return { kind: "found", list: await getList(id) };
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 404) return { kind: "gone", id };
    return isDenied(caught instanceof Error ? caught : null)
      ? { kind: "denied" }
      : { kind: "unnamed" };
  }
}

/**
 * Names the lists a set of campaigns point at, reading by id whichever `known` does not already
 * hold. A refusal or a failure is neither found nor gone: the id is simply left unnamed.
 *
 * It spends as few calls as the answer allows. Nothing is read when `skip` is set — the paged read
 * of the same lists was just refused for who is asking, and a by-id read needs the same permission.
 * Otherwise **one** id is read first: a caller who may not read lists learns it from one refusal
 * rather than from every read already in flight, and the rest are read at most `BY_ID_IN_FLIGHT` at
 * a time, and only if that first read was not refused.
 */
export async function resolveLists(
  ids: readonly string[],
  known: readonly ContactListOut[],
  options: { skip?: boolean } = {},
): Promise<ResolvedLists> {
  const have = new Set(known.map((list) => list.id));
  const wanted = [...new Set(ids)].filter((id) => !have.has(id));
  const [probe, ...rest] = wanted;
  if (options.skip === true || probe === undefined) return { lists: [...known], gone: new Set() };

  const first = await readListById(probe);
  const answers = [
    first,
    ...(first.kind === "denied" ? [] : await mapLimited(rest, BY_ID_IN_FLIGHT, readListById)),
  ];

  const found: ContactListOut[] = [];
  const gone = new Set<string>();
  for (const answer of answers) {
    if (answer.kind === "found") found.push(answer.list);
    else if (answer.kind === "gone") gone.add(answer.id);
  }
  return { lists: [...known, ...found], gone };
}

/** What a campaign is addressed to, in words that claim only what was actually learned. */
export function audienceName(campaign: CampaignOut, resolved: ResolvedLists): string {
  if (campaign.audience === "all_sendable") return "Everyone you may mail";
  const list = resolved.lists.find((l) => l.id === campaign.list_id);
  if (list !== undefined) return list.name;
  if (campaign.list_id !== null && resolved.gone.has(campaign.list_id)) {
    return "A list that no longer exists";
  }
  return "A list";
}

/** The sender a campaign was written as, lower-cased where it is not a name so it reads inside a
 *  sentence ("as the sender chosen…") and, with `first-letter:uppercase`, on a line of its own.
 *  `no longer exists` only when every sender was read and this one is not among them. */
export function senderName(campaign: CampaignOut, senders: Picker<SenderOut>): string {
  const sender = senders.items.find((s) => s.id === campaign.sender_id);
  if (sender !== undefined) return `${sender.name} <${sender.address}>`;
  if (senders.error === null && !partial(senders)) return "a sender that no longer exists";
  return "the sender chosen for this campaign";
}

/** "Showing 200 of 2,500 lists" — said whenever a picker is a partial view of what exists. */
export function partialNotice(picker: Picker<unknown>, noun: string): string | null {
  if (!partial(picker)) return null;
  const format = new Intl.NumberFormat(undefined);
  return `Showing ${format.format(picker.loaded)} of ${format.format(picker.total)} ${noun}.`;
}
