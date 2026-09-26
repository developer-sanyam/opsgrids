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
 */
import { RowProblem, defineImportEntity } from "@opsgrids/client";
import type { ColumnOption, ImportColumn, Lookup } from "@opsgrids/client";

import { createContact, listContacts } from "@/api/client";
import type { Contact, ContactStatus } from "@/api/types";
import type { ContactIn } from "@/api/wire";

import {
  COMPANY_HINT,
  LOOKUP_ALL_OWNERS,
  LOOKUP_COMPANIES,
  LOOKUP_CONTACT_EMAILS,
  LOOKUP_OWNERS,
  OWNER_HINT,
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
    required: true,
    maxLength: 254,
    help: "Their email address. A contact whose exact address is already on file is skipped; one that differs only in capitals is refused.",
  },
  {
    key: "company",
    header: "Company",
    kind: "text",
    required: true,
    lookup: LOOKUP_COMPANIES,
    lookupHint: COMPANY_HINT,
    help: "The name of a company that already exists, spelled as on the Companies screen. See the Lists sheet.",
  },
  {
    key: "owner",
    header: "Owner email",
    kind: "text",
    lookup: LOOKUP_OWNERS,
    lookupHint: OWNER_HINT,
    help: "Email of the team member who owns the contact, from the Lists sheet. Leave blank to own it yourself.",
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
    help: "Where the contact sits in your funnel. Leave blank for Lead.",
  },
  {
    key: "phone",
    header: "Phone",
    kind: "phone",
    help: "With the country code, like +91 98765 43210. A number without one is refused. Left blank on export: the number is never shown.",
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
    help: "The record's id. Filled in on export. A row that carries one is refused on import: it is already in the system.",
  },
  {
    key: "createdAt",
    header: "Created",
    kind: "text",
    readOnly: true,
    help: "When the contact was added. Filled in on export and ignored on import.",
  },
];

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
    "Import only adds contacts. A contact whose exact email is already on file is skipped, not updated.",
    "Emails are matched exactly, as the server matches them. An address that differs from one on file only in capitals is refused, so the same person is not added twice.",
    "Phone numbers need a country code, like +91 98765 43210. A number without one is refused and reported.",
    "Leave Owner email blank to own the contact yourself. If your role limits you to your own contacts, you can only assign contacts to yourself.",
    "Exports show phone numbers masked, as the app does.",
    "A file exported from the Contacts list cannot be imported again as it is: its rows carry a Contact ID and are refused, because those contacts already exist.",
    "An organisation with more than 20,000 contacts cannot import contacts in this release: the addresses already on file cannot all be checked, so nothing is imported.",
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
    const email = requiredTextOf(row, "email");
    const problem = capitalsProblem(email, ctx.lookups[LOOKUP_CONTACT_EMAILS]);
    if (problem !== null) throw new RowProblem(problem);
    const phone = textOf(row, "phone");
    return {
      full_name: requiredTextOf(row, "fullName"),
      email,
      company_id: companyIdByName(requiredTextOf(row, "company"), ctx),
      owner_id: ownerIdOrSelf(row, ctx),
      title: textOf(row, "title") ?? "",
      status: optionOf(row, "status", STATUS_OPTIONS) ?? "lead",
      ...(phone !== undefined ? { raw_phone: phone } : {}),
    };
  },
  create: (payload) => createContact(payload),
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
