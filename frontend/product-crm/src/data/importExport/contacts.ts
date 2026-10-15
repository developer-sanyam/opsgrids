/**
 * Contacts from and to a spreadsheet (ADR-0022). Import calls `POST /contacts` once per row: the
 * company must already exist, an email that is already on file is a 409 and is skipped, and a
 * caller limited to their own book may only assign contacts to themselves.
 *
 * The server dedupes contacts on the exact email string, so `Asha.Rao@x.co` and `asha.rao@x.co`
 * are two contacts to it, and importing the second would put one person on file twice. An address
 * that matches one on file exactly still goes to the server, whose 409 is reported as skipped; one
 * that differs from a stored address only in capitals is refused before anything is sent.
 *
 * A contact's phone number is masked on the wire and never revealed here: the export writes the
 * mask in a read-only column, and the importable Phone column is left blank on export.
 *
 * Update calls `PATCH /contacts/{id}` with only the fields that differ, and finds a contact with no
 * Contact ID by its email, the server's unique key, compared exactly as stored (the default
 * `keyMatch`): an address that differs from a stored one only in capitals is another contact to the
 * server, so the engine refuses the row rather than pick one. The PATCH does not take the
 * phone: a non-blank Phone cell is one write-only `PUT /contacts/{id}/phone`, because the stored
 * number is only a mask and cannot be compared. The server does not stop a PATCH from changing an
 * email to another contact's address in other capitals, so `check` does, as it does when adding.
 */
import { RowProblem, defineImportEntity } from "@opsgrids/client";
import type { ColumnOption, ImportColumn, Lookup, ParsedValue } from "@opsgrids/client";

import { createContact, listContacts, setContactPhone, updateContact } from "@/api/client";
import type { Contact, ContactStatus } from "@/api/types";
import type { ContactIn, ContactPatch } from "@/api/wire";

import {
  COMPANY_HINT,
  COMPANY_HINT_UPDATE,
  LOOKUP_ALL_OWNERS,
  LOOKUP_COMPANIES,
  LOOKUP_CONTACT_EMAILS,
  LOOKUP_OWNERS,
  OWNER_HINT,
  OWNER_HINT_UPDATE,
  companyIdByName,
  labelFor,
  loadCompanyLookup,
  loadExistingContactEmails,
  loadOwnerLookups,
  optionLabel,
  optionOf,
  ownerIdOrSelf,
  requiredTextOf,
  textOf,
} from "./shared";
import { columnOf, loadEvery, orNull, patchOf, refuseInactiveOwner, resolveByLookup } from "./updateShared";

/** What the Contacts screen filters its list by; the export writes exactly that list. */
export interface ContactFilters {
  search: string;
  status: ContactStatus | "all";
}

const STATUS_OPTIONS: readonly (ColumnOption & { value: ContactStatus })[] = [
  { value: "lead", label: "Lead" },
  { value: "qualified", label: "Qualified" },
  { value: "customer", label: "Customer" },
  { value: "dormant", label: "Dormant" },
];

const COLUMNS: readonly ImportColumn[] = [
  {
    key: "fullName",
    header: "Full name",
    kind: "text",
    required: true,
    maxLength: 160,
    help: "The contact's full name.",
  },
  {
    key: "email",
    header: "Email",
    kind: "email",
    maxLength: 254,
    help: "Their email address. Optional: a contact needs an Email or a Phone. When adding, a contact whose exact address is already on file is skipped and one that differs only in capitals is refused. When updating, a row with no Contact ID finds its contact by this address.",
  },
  {
    key: "company",
    header: "Company",
    kind: "text",
    required: true,
    lookup: LOOKUP_COMPANIES,
    lookupHint: COMPANY_HINT,
    lookupHintUpdate: COMPANY_HINT_UPDATE,
    help: "The name of a company that already exists, spelled as on the Companies screen. See the Lists sheet.",
  },
  {
    key: "owner",
    header: "Owner email",
    kind: "text",
    lookup: LOOKUP_OWNERS,
    lookupHint: OWNER_HINT,
    lookupHintUpdate: OWNER_HINT_UPDATE,
    help: "Email of the team member who owns the contact, from the Lists sheet. Leave blank to own it yourself, or, when updating, to keep the current owner.",
  },
  {
    key: "title",
    header: "Title",
    kind: "text",
    maxLength: 160,
    help: "Their job title, like VP Operations.",
  },
  {
    key: "status",
    header: "Status",
    kind: "enum",
    options: STATUS_OPTIONS,
    help: "Where the contact sits in your funnel. Leave blank for Lead, or, when updating, to keep the current status.",
  },
  {
    key: "phone",
    header: "Phone",
    kind: "phone",
    help: "With the country code, like +91 98765 43210. For an Indian organization a 10-digit mobile is also accepted and read as +91. A contact needs a Phone or an Email. Left blank on export: the number is never shown. When updating, a number here replaces the one on file, and a blank leaves it as it is.",
  },
  {
    key: "phoneMasked",
    header: "Phone (masked)",
    kind: "text",
    readOnly: true,
    help: "The number as the app shows it, with most digits hidden. Filled in on export and ignored on import.",
  },
  {
    key: "lastActivity",
    header: "Last activity",
    kind: "text",
    readOnly: true,
    help: "When the contact was last logged against. Filled in on export and ignored on import.",
  },
  {
    key: "id",
    header: "Contact ID",
    kind: "text",
    readOnly: true,
    help: "The record's id. Filled in on export. With Add new records only, a row that carries one is refused on import: it is already in the system. With Add new and update existing, it names the contact to update.",
  },
  {
    key: "createdAt",
    header: "Created",
    kind: "text",
    readOnly: true,
    help: "When the contact was added. Filled in on export and ignored on import.",
  },
];

const EMAIL = columnOf(COLUMNS, "email");
const COMPANY = columnOf(COLUMNS, "company");
const OWNER = columnOf(COLUMNS, "owner");
const PHONE = columnOf(COLUMNS, "phone");

/** Two addresses that differ only in capitals are one person to the app (`capitalsProblem` says
 *  so), so an email cell that is a different spelling of the stored address changes nothing and
 *  the address stays exactly as it is on file. */
function sameAddress(cell: ParsedValue, stored: ParsedValue): boolean {
  return typeof cell === "string" && typeof stored === "string" && cell.toLowerCase() === stored.toLowerCase();
}

/**
 * Why this address cannot be sent, or null when it can. An address that is on file exactly as
 * typed is fine (the server answers 409, which is reported as skipped); one that matches a stored
 * address ignoring capitals, but not exactly, would be a second contact for the same person.
 */
function capitalsProblem(email: string, onFile: Lookup | undefined): string | null {
  if (onFile === undefined || onFile.labelOf(email) !== undefined) return null;
  const stored = onFile.resolve(email);
  if (stored !== undefined) {
    return `A contact with this email is already on file as ${stored}; addresses that differ only in capitals are treated as the same person.`;
  }
  if (onFile.isAmbiguous(email)) {
    return "A contact with this email is already on file in other capitals; addresses that differ only in capitals are treated as the same person.";
  }
  return null;
}

export const contactImport = defineImportEntity<ContactIn, Contact, ContactFilters>({
  id: "crm.contacts",
  noun: "contacts",
  nounSingular: "contact",
  fileStem: "contacts",
  columns: COLUMNS,
  sampleRows: [
    {
      fullName: "Asha Rao",
      email: "asha.rao@northwind.co",
      company: "Northwind Logistics",
      title: "VP Operations",
      status: "Customer",
      phone: "+91 98765 43210",
    },
    {
      fullName: "Daniel Okafor",
      email: "daniel.okafor@cobaltfin.com",
      company: "Cobalt Financial",
      title: "Head of Procurement",
      status: "Qualified",
      phone: "+234 802 345 6789",
    },
  ],
  notes: [
    "The company must already exist. If it is new, import it on the Companies screen first.",
    "With Add new records only, a contact whose exact email is already on file is skipped, not updated.",
    "Emails are matched exactly, as the server matches them. An address that differs from one on file only in capitals is refused, so the same person is not added twice.",
    "Phone numbers need a country code, like +91 98765 43210. A number without one is refused and reported.",
    "When adding, leave Owner email blank to own the contact yourself. If your role limits you to your own contacts, you can only assign contacts to yourself.",
    "Exports show phone numbers masked, as the app does.",
    "With Add new records only, a file exported from the Contacts list cannot be imported again as it is: its rows carry a Contact ID and are refused, because those contacts already exist.",
    "An organisation with more than 20,000 contacts cannot import contacts in this release: the addresses already on file cannot all be checked, so nothing is imported.",
    "When updating, a row with a Contact ID changes that contact. Without one, the contact is found by its exact Email, capitals included, and an Email that differs from a contact's on file only in capitals is refused, so a row never changes a contact it only resembles. To change a contact's email keep its Contact ID in the row: a row with a new address and no Contact ID is a new contact.",
    "When updating a row that has a Contact ID, an Email that differs from the one on file only in capitals is treated as unchanged and the address stays as it is. Changing it to another contact's address in other capitals is refused; to another contact's exact address, the server refuses it and it is reported.",
    "When updating, type [clear] in Title to empty it. Full name, Email, Company, Owner email and Status cannot be emptied, only changed.",
    "When updating, a Phone in the row is always saved to the contact, even if it is the number already on file: the stored number is hidden, so it cannot be compared. Leave Phone blank to keep the number. A phone number cannot be removed, only replaced.",
  ],
  recordIdKey: "id",
  loadLookups: async (signal) => {
    const [owners, companies, emails] = await Promise.all([
      loadOwnerLookups(signal),
      loadCompanyLookup(signal),
      loadExistingContactEmails(signal),
    ]);
    return { ...owners, [LOOKUP_COMPANIES]: companies, [LOOKUP_CONTACT_EMAILS]: emails };
  },
  toPayload: (row, ctx) => {
    // Optional (ADR-0038 D-14): a person reachable by phone alone is a contact, as on the form.
    const email = textOf(row, "email");
    const phone = textOf(row, "phone");
    if (email === undefined && phone === undefined) {
      throw new RowProblem("Add an Email or a Phone so you can reach this contact");
    }
    if (email !== undefined) {
      const problem = capitalsProblem(email, ctx.lookups[LOOKUP_CONTACT_EMAILS]);
      if (problem !== null) throw new RowProblem(problem);
    }
    return {
      full_name: requiredTextOf(row, "fullName"),
      ...(email !== undefined ? { email } : {}),
      company_id: companyIdByName(requiredTextOf(row, "company"), ctx),
      owner_id: ownerIdOrSelf(row, ctx),
      title: textOf(row, "title") ?? "",
      status: optionOf(row, "status", STATUS_OPTIONS) ?? "lead",
      ...(phone !== undefined ? { raw_phone: phone } : {}),
    };
  },
  create: (payload) => createContact(payload),
  update: {
    loadExisting: (ctx) => loadEvery(listContacts, ctx),
    idOf: (contact) => contact.id,
    keyColumn: "email",
    keyOf: (contact) => contact.email,
    recordLabel: (contact) => contact.full_name,
    fields: [
      { column: "fullName", field: "full_name", current: (contact) => contact.full_name },
      { column: "email", current: (contact) => contact.email, same: sameAddress },
      {
        column: "company",
        field: "company_id",
        current: (contact) => contact.company_id,
        resolve: resolveByLookup(COMPANY),
      },
      {
        column: "owner",
        field: "owner_id",
        current: (contact) => contact.owner_id,
        resolve: resolveByLookup(OWNER, LOOKUP_ALL_OWNERS),
      },
      { column: "title", current: (contact) => orNull(contact.title), clear: "" },
      { column: "status", current: (contact) => contact.status },
    ],
    check: (row, _contact, changed, ctx) => {
      refuseInactiveOwner(OWNER, row, changed, ctx);
      const email = textOf(row, "email");
      if (email === undefined || !changed.includes(EMAIL.header)) return;
      const problem = capitalsProblem(email, ctx.lookups[LOOKUP_CONTACT_EMAILS]);
      if (problem !== null) throw new RowProblem(problem);
    },
    send: async (contact, body) => {
      await updateContact(contact.id, patchOf<ContactPatch>(body));
    },
    extraColumns: ["phone"],
    // The number is write-only: the stored one is a mask, so a filled Phone cell always differs
    // and is always written, and there is no route that removes a number.
    extras: (row, cleared, contact) => {
      if (cleared.has(PHONE.key)) {
        throw new RowProblem(`${PHONE.header} cannot be cleared: a saved number can be replaced, not removed`);
      }
      const phone = textOf(row, "phone");
      if (phone === undefined) return [];
      return [
        {
          changed: [PHONE.header],
          run: async () => {
            await setContactPhone(contact.id, { raw_phone: phone });
          },
        },
      ];
    },
  },
  export: {
    fetchPage: ({ page, size, filters, signal }) =>
      listContacts({ search: filters.search, status: filters.status, page, size, signal }),
    toRow: (contact, lookups) => ({
      fullName: contact.full_name,
      email: contact.email,
      company: labelFor(lookups[LOOKUP_COMPANIES], contact.company_id),
      owner: labelFor(lookups[LOOKUP_ALL_OWNERS], contact.owner_id),
      title: contact.title,
      status: optionLabel(STATUS_OPTIONS, contact.status),
      phone: null,
      phoneMasked: contact.phone,
      lastActivity: contact.last_activity_at,
      id: contact.id,
      createdAt: contact.created_at,
    }),
  },
});
