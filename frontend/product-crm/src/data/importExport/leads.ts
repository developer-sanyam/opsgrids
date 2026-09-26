/**
 * Leads from and to a spreadsheet (ADR-0022). Import calls `POST /leads` once per row. A lead is
 * always captured as `new` from the source `import`; an email that is already on file is a 409 and
 * is skipped; a lead with no owner is assigned by the organisation's round-robin rule, exactly as
 * the "Capture lead" form does with its Auto-assign choice.
 *
 * A lead's email and phone are always masked on the wire and never revealed here: the export
 * writes the masks in read-only columns, and the importable Email and Phone columns are left blank
 * on export.
 */
import { defineImportEntity } from "@opsgrids/client";
import type { ColumnOption, ImportColumn } from "@opsgrids/client";

import { createLead, listLeads } from "@/api/client";
import type { Lead, LeadSource, LeadStatus } from "@/api/types";
import type { LeadIn } from "@/api/wire";

import {
  LOOKUP_OWNERS,
  loadOwnerLookup,
  namedOwnerId,
  optionLabel,
  ownerEmail,
  phoneOf,
  requiredTextOf,
  textOf,
} from "./shared";

/** What the Leads screen filters its list by; the export writes exactly that list. */
export interface LeadFilters {
  status: LeadStatus | "all";
}

const STATUS_OPTIONS: readonly (ColumnOption & { value: LeadStatus })[] = [
  { value: "new", label: "New" },
  { value: "working", label: "Working" },
  { value: "qualified", label: "Qualified" },
  { value: "disqualified", label: "Disqualified" },
  { value: "converted", label: "Converted" },
];

const SOURCE_OPTIONS: readonly (ColumnOption & { value: LeadSource })[] = [
  { value: "manual", label: "Manual" },
  { value: "form", label: "Web form" },
  { value: "import", label: "Import" },
];

/** Every imported lead carries this source, so the intake can tell them from typed-in ones. */
const IMPORT_SOURCE: LeadSource = "import";

const COLUMNS: readonly ImportColumn[] = [
  {
    key: "fullName",
    header: "Full name",
    kind: "text",
    required: true,
    maxLength: 160,
    help: "The lead's full name.",
  },
  {
    key: "company",
    header: "Company",
    kind: "text",
    maxLength: 200,
    help: "The company the lead works for, as they gave it. Plain text: it does not have to exist on the Companies screen.",
  },
  {
    key: "email",
    header: "Email",
    kind: "email",
    maxLength: 254,
    help: "Their email address. A lead whose email is already on file is skipped. Left blank on export: the address is never shown.",
  },
  {
    key: "phone",
    header: "Phone",
    kind: "phone",
    help: "With the country code, like +91 98765 43210. A number without one is refused. Left blank on export: the number is never shown.",
  },
  {
    key: "owner",
    header: "Owner email",
    kind: "text",
    lookup: LOOKUP_OWNERS,
    help: "Email of the team member who owns the lead, from the Lists sheet. Leave blank to have it assigned by your round-robin rule.",
  },
  {
    key: "notes",
    header: "Notes",
    kind: "text",
    maxLength: 4000,
    help: "Anything worth knowing about the lead.",
  },
  {
    key: "status",
    header: "Status",
    kind: "text",
    readOnly: true,
    help: "Where the lead is in the intake. Filled in on export and ignored on import: every imported lead starts as New.",
  },
  {
    key: "source",
    header: "Source",
    kind: "text",
    readOnly: true,
    help: "Where the lead came from. Filled in on export and ignored on import: every imported lead gets the source Import.",
  },
  {
    key: "emailMasked",
    header: "Email (masked)",
    kind: "text",
    readOnly: true,
    help: "The address as the app shows it, with most characters hidden. Filled in on export and ignored on import.",
  },
  {
    key: "phoneMasked",
    header: "Phone (masked)",
    kind: "text",
    readOnly: true,
    help: "The number as the app shows it, with most digits hidden. Filled in on export and ignored on import.",
  },
  {
    key: "id",
    header: "Lead ID",
    kind: "text",
    readOnly: true,
    help: "The record's id. Filled in on export and ignored on import.",
  },
  {
    key: "createdAt",
    header: "Captured",
    kind: "text",
    readOnly: true,
    help: "When the lead was captured. Filled in on export and ignored on import.",
  },
];

export const leadImport = defineImportEntity<LeadIn, Lead, LeadFilters>({
  id: "crm.leads",
  noun: "leads",
  nounSingular: "lead",
  fileStem: "leads",
  columns: COLUMNS,
  sampleRows: [
    {
      fullName: "Meera Iyer",
      company: "Halcyon Textiles",
      email: "meera.iyer@halcyontextiles.in",
      phone: "+91 90000 12345",
      notes: "Asked for a demo after the Chennai trade show.",
    },
    {
      fullName: "Tomas Berg",
      company: "Fjord Freight",
      email: "tomas.berg@fjordfreight.example",
      phone: "+47 912 34 567",
      notes: "Referred by an existing customer.",
    },
  ],
  notes: [
    "Every imported lead starts as New with the source Import.",
    "Leave Owner email blank and your organisation's round-robin rule assigns the lead. If your role limits you to your own leads, you can only name yourself.",
    "A lead whose email is already on file is skipped. A lead with no email cannot be checked, so importing the same file twice adds those leads twice.",
    "Phone numbers need a country code, like +91 98765 43210. A number without one is refused and reported.",
    "Exports show email and phone masked, as the app does. The masked columns are ignored if you import that file again.",
  ],
  loadLookups: async (signal) => ({ [LOOKUP_OWNERS]: await loadOwnerLookup(signal) }),
  toPayload: (row, ctx) => {
    const owner = namedOwnerId(row, ctx);
    const email = textOf(row, "email");
    const phone = phoneOf(row, "phone");
    return {
      full_name: requiredTextOf(row, "fullName", "Full name"),
      company: textOf(row, "company") ?? null,
      email: email ?? null,
      phone: phone ?? null,
      source: IMPORT_SOURCE,
      ...(owner !== undefined ? { owner_id: owner } : {}),
      notes: textOf(row, "notes") ?? "",
    };
  },
  create: (payload) => createLead(payload),
  export: {
    fetchPage: ({ page, size, filters, signal }) =>
      listLeads({ status: filters.status, page, size, signal }),
    toRow: (lead, lookups) => ({
      fullName: lead.full_name,
      company: lead.company,
      email: null,
      phone: null,
      owner: ownerEmail(lookups[LOOKUP_OWNERS], lead.owner_id),
      notes: lead.notes,
      status: optionLabel(STATUS_OPTIONS, lead.status),
      source: optionLabel(SOURCE_OPTIONS, lead.source),
      emailMasked: lead.email,
      phoneMasked: lead.phone,
      id: lead.id,
      createdAt: lead.created_at,
    }),
  },
});
