/**
 * What the four CRM import entities (companies, contacts, leads, deals) have in common: the
 * reference lookups built from the full lists, reading a coerced row, and writing a list item
 * back into a sheet row for the export.
 *
 * The lookups are built from every page of the list (`collectPages`), never from the 200-row
 * `DirectoryProvider` the screens use for display: a book past 200 companies would otherwise have
 * its real companies called unknown.
 */
import { collectPages, makeLookup, RowProblem } from "@opsgrids/client";
import type { ColumnOption, ImportContext, Lookup, LookupSet, ParsedRow } from "@opsgrids/client";

import { getOwners, listCompanies, listContacts } from "@/api/client";
import type { Owner } from "@/api/types";
import { getMySubjectRef } from "@/lib/subject";

export const LOOKUP_OWNERS = "owners";
export const LOOKUP_COMPANIES = "companies";
export const LOOKUP_CONTACTS = "contacts";
/** Contact email to the id of the company that contact belongs to. Not shown to the user: it lets
 *  a deal row check that its primary contact belongs to its company. */
export const LOOKUP_CONTACT_COMPANIES = "contactCompanies";

/** The list endpoints order by a name, and names tie: offset paging can then hand the same row
 *  out twice, which would make a real email or name look ambiguous. Keep the first of each id. */
function distinctById<T extends { id: string }>(items: readonly T[]): T[] {
  const seen = new Set<string>();
  const kept: T[] = [];
  for (const item of items) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    kept.push(item);
  }
  return kept;
}

/* --------------------------------- lookups --------------------------------- */

/** Owners by email. Only active reps: the server refuses an assignment to a deactivated one. */
export async function loadOwnerLookup(signal: AbortSignal): Promise<Lookup> {
  const owners: Owner[] = await getOwners(signal);
  return makeLookup(
    owners.filter((o) => o.is_active).map((o) => ({ label: o.email, value: o.id })),
  );
}

/** Companies by name (unique per organisation, ignoring case). */
export async function loadCompanyLookup(signal: AbortSignal): Promise<Lookup> {
  const rows = await collectPages((page, size) => listCompanies({ page, size, signal }), { signal });
  return makeLookup(distinctById(rows).map((c) => ({ label: c.name, value: c.id })));
}

/**
 * Contacts by email (unique per organisation), valued as the contact's id, and a second lookup
 * from the same emails to the company each contact belongs to. The deal service cannot see the
 * customer schema, so it accepts any contact id and would store one from another company without
 * complaint; a deal row is checked against the second lookup before anything is sent.
 */
export async function loadContactLookups(signal: AbortSignal): Promise<LookupSet> {
  const rows = distinctById(await collectPages((page, size) => listContacts({ page, size, signal }), { signal }));
  return {
    [LOOKUP_CONTACTS]: makeLookup(rows.map((c) => ({ label: c.email, value: c.id }))),
    [LOOKUP_CONTACT_COMPANIES]: makeLookup(rows.map((c) => ({ label: c.email, value: c.company_id }))),
  };
}

/* ---------------------------- reverse lookups (export) ---------------------------- */

/** What the sheet shows for a reference: the label (email or name) the lookup holds for the id, or
 *  blank when the id is blank or the lookup does not know it. */
export function labelFor(lookup: Lookup | undefined, id: string | null): string | null {
  if (lookup === undefined || id === null) return null;
  return lookup.labelOf(id) ?? null;
}

/* ------------------------------- reading a row ------------------------------- */

/** A non-blank text cell, trimmed, or undefined. */
export function textOf(row: ParsedRow, key: string): string | undefined {
  const value = row[key];
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed;
}

/** A required text cell. The engine has already refused a blank one; this is what lets the
 *  payload type say `string`. */
export function requiredTextOf(row: ParsedRow, key: string, what: string): string {
  const value = textOf(row, key);
  if (value === undefined) throw new RowProblem(`${what} is required.`);
  return value;
}

export function numberOf(row: ParsedRow, key: string): number | undefined {
  const value = row[key];
  return typeof value === "number" ? value : undefined;
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

const E164 = /^\+[1-9]\d{7,14}$/;

/** A phone with its country code, as the API takes it (`+` and digits). The importer never
 *  guesses a country code, and the server refuses a number without one. */
export function phoneOf(row: ParsedRow, key: string): string | undefined {
  const phone = textOf(row, key);
  if (phone === undefined) return undefined;
  if (!E164.test(phone)) {
    throw new RowProblem(
      `"${phone}" is not a phone number with a country code. Write it like +91 98765 43210.`,
    );
  }
  return phone;
}

/* ------------------------------ resolving names ------------------------------ */

/** The owner the Owner email cell names, or undefined when it is blank. */
export function namedOwnerId(row: ParsedRow, ctx: ImportContext): string | undefined {
  const email = textOf(row, "owner");
  if (email === undefined) return undefined;
  const lookup = ctx.lookups[LOOKUP_OWNERS];
  const id = lookup?.resolve(email);
  if (id !== undefined) return id;
  throw new RowProblem(
    lookup?.isAmbiguous(email) === true
      ? `More than one team member has the email "${email}".`
      : `No active team member has the email "${email}". Pick one from the Lists sheet.`,
  );
}

/**
 * The owner for a record that must have one: the named rep, or, when the cell is blank, the
 * caller's own rep record (the same "which rep am I" fact the Overview's "My pipeline" uses, and
 * what the deal service falls back to itself). Whether the caller may assign someone else is the
 * server's call, reported per row.
 */
export function ownerIdOrSelf(row: ParsedRow, ctx: ImportContext): string {
  const named = namedOwnerId(row, ctx);
  if (named !== undefined) return named;
  const self = getMySubjectRef();
  if (self === null) {
    throw new RowProblem(
      "Owner email is blank and your account has no team-member record to use instead. Fill in Owner email.",
    );
  }
  return self;
}

/** The id of the company with this name, from the full list of companies. */
export function companyIdByName(name: string, ctx: ImportContext): string {
  const lookup = ctx.lookups[LOOKUP_COMPANIES];
  const id = lookup?.resolve(name);
  if (id !== undefined) return id;
  throw new RowProblem(
    lookup?.isAmbiguous(name) === true
      ? `More than one company is named "${name}". Rename one of them, then import again.`
      : `No company is named "${name}". Check the spelling against the Lists sheet, or add the company first.`,
  );
}

/** The contact the given email names, with the company that contact belongs to. */
export function contactRefOf(
  email: string,
  ctx: ImportContext,
): { companyId: string; contactId: string } {
  const lookup = ctx.lookups[LOOKUP_CONTACTS];
  const contactId = lookup?.resolve(email);
  const companyId = ctx.lookups[LOOKUP_CONTACT_COMPANIES]?.resolve(email);
  if (contactId !== undefined && companyId !== undefined) return { companyId, contactId };
  throw new RowProblem(
    lookup?.isAmbiguous(email) === true
      ? `More than one contact has the email "${email}".`
      : `No contact has the email "${email}". Check it against the Lists sheet, or add the contact first.`,
  );
}
