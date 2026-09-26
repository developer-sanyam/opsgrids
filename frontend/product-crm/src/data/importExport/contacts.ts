/**
 * Contacts from and to a spreadsheet (ADR-0022). Import calls `POST /contacts` once per row: the
 * company must already exist, an email that is already on file is a 409 and is skipped, and a
 * caller limited to their own book may only assign contacts to themselves.
 *
 * A contact's phone number is masked on the wire and never revealed here: the export writes the
 * mask in a read-only column, and the importable Phone column is left blank on export.
 */
import { defineImportEntity } from "@opsgrids/client";
import type { ColumnOption, ImportColumn } from "@opsgrids/client";

import { createContact, listContacts } from "@/api/client";
import type { Contact, ContactStatus } from "@/api/types";
import type { ContactIn } from "@/api/wire";

import {
  LOOKUP_COMPANIES,
  LOOKUP_OWNERS,
  companyIdByName,
  labelFor,
  loadCompanyLookup,
  loadOwnerLookup,
  optionLabel,
  optionOf,
  ownerIdOrSelf,
  phoneOf,
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
    help: "Their email address. It must be new: a contact with the same email is skipped.",
  },
  {
    key: "company",
    header: "Company",
    kind: "text",
    required: true,
    lookup: LOOKUP_COMPANIES,
    help: "The name of a company that already exists, spelled as on the Companies screen. See the Lists sheet.",
  },
  {
    key: "owner",
    header: "Owner email",
    kind: "text",
    lookup: LOOKUP_OWNERS,
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
    help: "The record's id. Filled in on export and ignored on import.",
  },
  {
    key: "createdAt",
    header: "Created",
    kind: "text",
    readOnly: true,
    help: "When the contact was added. Filled in on export and ignored on import.",
  },
];

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
    "Import only adds contacts. A contact whose email is already on file is skipped, not updated.",
    "Phone numbers need a country code, like +91 98765 43210. A number without one is refused and reported.",
    "Leave Owner email blank to own the contact yourself. If your role limits you to your own contacts, you can only assign contacts to yourself.",
    "Exports show phone numbers masked, as the app does. The masked column is ignored if you import that file again.",
  ],
  loadLookups: async (signal) => {
    const [owners, companies] = await Promise.all([loadOwnerLookup(signal), loadCompanyLookup(signal)]);
    return { [LOOKUP_OWNERS]: owners, [LOOKUP_COMPANIES]: companies };
  },
  toPayload: (row, ctx) => {
    const phone = phoneOf(row, "phone");
    return {
      full_name: requiredTextOf(row, "fullName", "Full name"),
      email: requiredTextOf(row, "email", "Email"),
      company_id: companyIdByName(requiredTextOf(row, "company", "Company"), ctx),
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
      owner: labelFor(lookups[LOOKUP_OWNERS], contact.owner_id),
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
