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
 */

/** Pages read for a picker: `MAX_PAGES` × `FULL_PAGE` rows before it becomes a partial view. */
export const MAX_PAGES = 5;

export interface Loaded<T> {
  items: T[];
  /** The service's own count — how many exist, not how many were read. */
  total: number;
}

async function readEveryPage<T>(read: (page: number) => Promise<Page<T>>): Promise<Loaded<T>> {
  const first = await read(1);
  const wanted = Math.min(first.pages, MAX_PAGES);
  const rest =
    wanted > 1
      ? await Promise.all(Array.from({ length: wanted - 1 }, (_, index) => read(index + 2)))
      : [];
  return { items: [first, ...rest].flatMap((page) => page.items), total: first.total };
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

export function loadLists(): Promise<Picker<ContactListOut>> {
  return settle(() => readEveryPage((page) => listLists({ page, size: FULL_PAGE })));
}

export async function loadRefs(): Promise<Refs> {
  const [senders, domains, lists] = await Promise.all([
    settle(() => readEveryPage((page) => listSenders({ page }))),
    settle(() => readEveryPage((page) => listDomains({ page }))),
    loadLists(),
  ]);
  return { senders, domains, lists };
}

export interface ResolvedLists {
  /** Every list this screen can name: the paged read, plus any read by id. */
  lists: ContactListOut[];
  /** Ids the service answered `404` for — the only lists that can be said to no longer exist. */
  gone: ReadonlySet<string>;
}

/**
 * Names the lists a set of campaigns point at, reading by id whichever the paged read did not
 * reach. A refusal or a failure is neither found nor gone: the id is simply left unnamed.
 */
export async function resolveLists(
  ids: readonly string[],
  known: readonly ContactListOut[],
): Promise<ResolvedLists> {
  const have = new Set(known.map((list) => list.id));
  const wanted = [...new Set(ids)].filter((id) => !have.has(id));
  const answers = await Promise.allSettled(wanted.map((id) => getList(id)));
  const found: ContactListOut[] = [];
  const gone = new Set<string>();
  answers.forEach((answer, index) => {
    const id = wanted[index];
    if (id === undefined) return;
    if (answer.status === "fulfilled") found.push(answer.value);
    else if (answer.reason instanceof ApiError && answer.reason.status === 404) gone.add(id);
  });
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
