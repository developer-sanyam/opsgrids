/**
 * What the four CRM import entities (companies, contacts, leads, deals) have in common: the
 * reference lookups built from the full lists, reading a coerced row, and writing a list item
 * back into a sheet row for the export.
 *
 * The lookups are built from every page of the list (`collectPages`), never from the 200-row
 * `DirectoryProvider` the screens use for display: a book past 200 companies would otherwise have
 * its real companies called unknown. `collectPages` is given each row's id, so a row the list
 * serves twice is kept once and a list that stays short of the server's total is refused.
 *
 * By the time `toPayload` runs, the engine has refused every blank required cell and every name
 * that is not in a lookup, with a sentence (and the column's `lookupHint`) the user can act on.
 * The helpers below therefore have nothing to say to a user about those: reaching one of those
 * cases is a bug in the engine or in a definition, and fails the row with the engine's generic
 * "could not be prepared" sentence instead of a message that could never be the right one.
 */
import { collectPages, makeLookup, RowProblem } from "@opsgrids/client";
import type { ColumnOption, ImportContext, Lookup, LookupSet, ParsedRow } from "@opsgrids/client";

import { getOwners, listCompanies, listContacts } from "@/api/client";
import type { Contact, Owner } from "@/api/types";
import { getMySubjectRef } from "@/lib/subject";

/** Active reps by email: what the Owner email column offers and accepts. */
export const LOOKUP_OWNERS = "owners";
/** Every rep by email, deactivated ones too. Used only to write an owner into an export, so it is
 *  attached to no column and never reaches the Lists sheet or a dropdown. */
export const LOOKUP_ALL_OWNERS = "ownersAll";
export const LOOKUP_COMPANIES = "companies";
export const LOOKUP_CONTACTS = "contacts";
/** Contact email to the id of the company that contact belongs to. Attached to no column: it lets
 *  a deal row check that its primary contact belongs to its company. */
export const LOOKUP_CONTACT_COMPANIES = "contactCompanies";
/** Contact id to the id of the company that contact belongs to. Attached to no column: an update
 *  uses it to check the contact a deal already has against a company the row changes it to. */
export const LOOKUP_COMPANY_OF_CONTACT = "companyOfContact";
/** Each existing contact email to itself, exactly as on file. Attached to no column: it lets a
 *  contact row tell an address that is already on file from one that differs only in capitals. */
export const LOOKUP_CONTACT_EMAILS = "contactEmails";

/* ------------------------------- what to tell a user ------------------------------- */

/** Added to the engine's "is not in the list" error on an Owner email column. */
export const OWNER_HINT =
  "Use the email of an active sales rep, see the Lists sheet. Leave blank to assign it to yourself.";
/** The same, for a list where a blank owner is assigned by the organisation's rule. */
export const OWNER_HINT_AUTO =
  "Use the email of an active sales rep, see the Lists sheet. Leave blank to assign automatically.";
export const COMPANY_HINT = "The company must already exist. Import companies first, then contacts and deals.";
export const CONTACT_HINT = "The contact must already exist and work at that company.";

/** The same hints for a row that updates a record, where a blank cell leaves the field alone
 *  instead of filling in a default (`ImportColumn.lookupHintUpdate`). */
export const OWNER_HINT_UPDATE =
  "Use the email of an active sales rep, see the Lists sheet. Leave blank to keep the current owner.";
export const COMPANY_HINT_UPDATE =
  "The company must already exist. Import companies first, then contacts and deals. Leave blank to keep the current company.";
export const CONTACT_HINT_UPDATE =
  "The contact must already exist and work at that company. Leave blank to keep the current contact, or type [clear] to remove it.";

/* --------------------------------- lookups --------------------------------- */

/**
 * A lookup that can also answer for a label's exact spelling. The engine matches labels ignoring
 * capitals, so two entries whose labels differ only in capitals (two contacts the server keeps apart
 * because it compares emails exactly) make each other's label ambiguous. `resolveSpelling` is how an
 * update settles that: see `resolveLabel` in `updateShared`.
 */
export interface SpellingLookup extends Lookup {
  /** The value of the one entry whose label is exactly `label` (trimmed, capitals as typed), or
   *  undefined when no entry, or more than one, is spelled that way. */
  resolveSpelling(label: string): string | undefined;
}

/** `makeLookup` that also knows each label's exact spelling. Everything `makeLookup` answers, it answers the same. */
export function makeSpellingLookup(entries: readonly { label: string; value: string }[]): SpellingLookup {
  const bySpelling = new Map<string, string | null>();
  for (const { label, value } of entries) {
    const spelling = label.trim();
    bySpelling.set(spelling, bySpelling.has(spelling) && bySpelling.get(spelling) !== value ? null : value);
  }
  return { ...makeLookup(entries), resolveSpelling: (label) => bySpelling.get(label.trim()) ?? undefined };
}

const entry = (o: Owner) => ({ label: o.email, value: o.id });

/**
 * The reps twice over: `owners` holds the active ones (the server refuses an assignment to a
 * deactivated rep, so it is what the column accepts), and `ownersAll` holds every one, so an
 * export can still name who owns a record whose rep has since left.
 */
export async function loadOwnerLookups(signal: AbortSignal): Promise<LookupSet> {
  const owners = await getOwners(signal);
  return {
    [LOOKUP_OWNERS]: makeSpellingLookup(owners.filter((o) => o.is_active).map(entry)),
    [LOOKUP_ALL_OWNERS]: makeSpellingLookup(owners.map(entry)),
  };
}

/** Companies by name (unique per organisation, ignoring case). */
export async function loadCompanyLookup(signal: AbortSignal): Promise<Lookup> {
  const rows = await collectPages((page, size) => listCompanies({ page, size, signal }), {
    signal,
    key: (company) => company.id,
  });
  return makeSpellingLookup(rows.map((c) => ({ label: c.name, value: c.id })));
}

/** Pages of contacts the contact-email lookup reads: 100 of 200, so 20,000 contacts. A book past
 *  that is refused loudly by `collectPages` rather than checked in part, and still fits the shared
 *  request budget beside the company lookup. */
export const CONTACT_EMAIL_PAGES = 100;

async function loadAllContacts(signal: AbortSignal, maxPages?: number): Promise<Contact[]> {
  return collectPages((page, size) => listContacts({ page, size, signal }), {
    signal,
    key: (contact) => contact.id,
    ...(maxPages !== undefined ? { maxPages } : {}),
  });
}

/**
 * Contacts by email, valued as the contact's id, and two lookups to the company each contact
 * belongs to: one from the email (what a sheet names) and one from the id (what a stored deal
 * holds). The deal service cannot see the customer schema, so it accepts any contact id and would
 * store one from another company without complaint; a deal row is checked against these before
 * anything is sent.
 */
export async function loadContactLookups(signal: AbortSignal): Promise<LookupSet> {
  const rows = await loadAllContacts(signal);
  return {
    [LOOKUP_CONTACTS]: makeSpellingLookup(rows.map((c) => ({ label: c.email, value: c.id }))),
    [LOOKUP_CONTACT_COMPANIES]: makeLookup(rows.map((c) => ({ label: c.email, value: c.company_id }))),
    [LOOKUP_COMPANY_OF_CONTACT]: makeLookup(rows.map((c) => ({ label: c.id, value: c.company_id }))),
  };
}

/**
 * The contact emails already on file, each valued as itself: `labelOf` says whether this exact
 * spelling exists (the server dedupes on the exact string), and `resolve` finds an address that
 * differs from a stored one only in capitals.
 */
export async function loadExistingContactEmails(signal: AbortSignal): Promise<Lookup> {
  const rows = await loadAllContacts(signal, CONTACT_EMAIL_PAGES);
  return makeLookup(rows.map((c) => ({ label: c.email, value: c.email })));
}

/* ---------------------------- reverse lookups (export) ---------------------------- */

/** What the sheet shows for a reference: the label (email or name) the lookup holds for the id, or
 *  blank when the id is blank or the lookup does not know it. */
export function labelFor(lookup: Lookup | undefined, id: string | null): string | null {
  if (lookup === undefined || id === null) return null;
  return lookup.labelOf(id) ?? null;
}

/* ------------------------------- reading a row ------------------------------- */

/** The engine let a row through that it should have refused. Not a sentence for the user. */
function refusedByEngine(what: string): never {
  throw new Error(`toPayload reached a row the engine should have refused: ${what}`);
}

/** A non-blank text cell, trimmed, or undefined. */
export function textOf(row: ParsedRow, key: string): string | undefined {
  const value = row[key];
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed;
}

/** A required text cell. The engine has already refused a blank one; this is what lets the
 *  payload type say `string`. */
export function requiredTextOf(row: ParsedRow, key: string): string {
  return textOf(row, key) ?? refusedByEngine(`${key} is blank`);
}

export function numberOf(row: ParsedRow, key: string): number | undefined {
  const value = row[key];
  return typeof value === "number" ? value : undefined;
}

/** A required number cell, on the same terms as `requiredTextOf`. */
export function requiredNumberOf(row: ParsedRow, key: string): number {
  return numberOf(row, key) ?? refusedByEngine(`${key} is blank`);
}

/** The option a coerced enum cell holds, or undefined when the cell is blank. */
export function optionOf<O extends { value: string }>(
  row: ParsedRow,
  key: string,
  options: readonly O[],
): O["value"] | undefined {
  const value = row[key];
  return options.find((o) => o.value === value)?.value;
}

/** What the sheet shows for an enum value: the option's label. */
export function optionLabel(options: readonly ColumnOption[], value: string): string {
  return options.find((o) => o.value === value)?.label ?? value;
}

/** ISO currency: upper-cased (the server does the same), INR when the row names none, which is
 *  also the currency the engine converts a money cell with when the currency column is blank. */
export function currencyOf(row: ParsedRow): string {
  return (textOf(row, "currency") ?? "INR").toUpperCase();
}

/* ------------------------------ resolving names ------------------------------ */

/** What a lookup column's cell resolves to. The engine has already refused a name the lookup does
 *  not hold (or holds twice), so a miss here is a bug, not a row for the user to fix. */
function resolved(lookup: Lookup | undefined, label: string, what: string): string {
  return lookup?.resolve(label) ?? refusedByEngine(`${what} "${label}" is not in its lookup`);
}

/** The owner the Owner email cell names, or undefined when it is blank. */
export function namedOwnerId(row: ParsedRow, ctx: ImportContext): string | undefined {
  const email = textOf(row, "owner");
  return email === undefined ? undefined : resolved(ctx.lookups[LOOKUP_OWNERS], email, "owner");
}

/**
 * The owner for a record that must have one: the named rep, or, when the cell is blank, the
 * caller's own rep record (the same "which rep am I" fact the Overview's "My pipeline" uses, and
 * what the deal service falls back to itself). That record has to be an active rep of the list
 * the column accepts, or the server would refuse the row with a less helpful sentence. Whether
 * the caller may assign someone else is the server's call, reported per row.
 */
export function ownerIdOrSelf(row: ParsedRow, ctx: ImportContext): string {
  const named = namedOwnerId(row, ctx);
  if (named !== undefined) return named;
  const self = getMySubjectRef();
  if (self === null || ctx.lookups[LOOKUP_OWNERS]?.labelOf(self) === undefined) {
    throw new RowProblem(
      "Your account has no active sales-rep record, so name an owner in the Owner email column.",
    );
  }
  return self;
}

/** The id of the company with this name, from the full list of companies. */
export function companyIdByName(name: string, ctx: ImportContext): string {
  return resolved(ctx.lookups[LOOKUP_COMPANIES], name, "company");
}

/** The contact the given email names, with the company that contact belongs to. */
export function contactRefOf(
  email: string,
  ctx: ImportContext,
): { companyId: string; contactId: string } {
  return {
    contactId: resolved(ctx.lookups[LOOKUP_CONTACTS], email, "contact"),
    companyId: resolved(ctx.lookups[LOOKUP_CONTACT_COMPANIES], email, "contact company"),
  };
}
