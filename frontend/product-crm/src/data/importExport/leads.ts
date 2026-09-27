/**
 * Leads from and to a spreadsheet (ADR-0022). Import calls `POST /leads` once per row. A lead is
 * always captured as `new` from the source `import`; an email that is already on file is a 409 and
 * is skipped; a lead with no owner is assigned by the organisation's round-robin rule, exactly as
 * the "Capture lead" form does with its Auto-assign choice.
 *
 * A lead's email and phone are always masked on the wire and never revealed here: the export
 * writes the masks in read-only columns, and the importable Email and Phone columns are left blank
 * on export.
 *
 * Update calls `PATCH /leads/{id}` with only the fields that differ. A lead has no usable key (its
 * email is sealed), so a row updates a lead only by its Lead ID; a row without one is added, as
 * when adding. Email and phone have no update route at all, so a cell in either refuses the row
 * instead of being ignored, and the status is a read-only column (moving it needs legal hops, and
 * `converted` is reachable only through conversion). A converted lead keeps its owner: the server
 * refuses any owner key on it, so `check` refuses the change before anything is sent.
 */
import { RowProblem, defineImportEntity } from "@opsgrids/client";
import type { ColumnOption, ImportColumn } from "@opsgrids/client";

import { createLead, listLeads, updateLead } from "@/api/client";
import type { Lead, LeadSource, LeadStatus } from "@/api/types";
import type { LeadIn, LeadPatch } from "@/api/wire";

import {
  LOOKUP_ALL_OWNERS,
  LOOKUP_OWNERS,
  OWNER_HINT_AUTO,
  OWNER_HINT_UPDATE,
  labelFor,
  loadOwnerLookups,
  namedOwnerId,
  optionLabel,
  requiredTextOf,
  textOf,
} from "./shared";
import {
  columnOf,
  loadEvery,
  orNull,
  patchOf,
  refuseInactiveOwner,
  resolveByLookup,
  sameMultiline,
} from "./updateShared";

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
    help: "Their email address, matched exactly as typed: a lead whose exact address is already on file is skipped. Left blank on export: the address is never shown. It cannot be changed on an existing lead: leave it blank when updating.",
  },
  {
    key: "phone",
    header: "Phone",
    kind: "phone",
    help: "With the country code, like +91 98765 43210. A number without one is refused. Left blank on export: the number is never shown. It cannot be changed on an existing lead: leave it blank when updating.",
  },
  {
    key: "owner",
    header: "Owner email",
    kind: "text",
    lookup: LOOKUP_OWNERS,
    lookupHint: OWNER_HINT_AUTO,
    lookupHintUpdate: OWNER_HINT_UPDATE,
    help: "Email of the team member who owns the lead, from the Lists sheet. Leave blank to have it assigned by your round-robin rule, or, when updating, to keep the current owner.",
  },
  {
    key: "notes",
    header: "Notes",
    kind: "text",
    maxLength: 4000,
    multiline: true,
    help: "Anything worth knowing about the lead. It may run over several lines.",
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
    help: "The record's id. Filled in on export. With Add new records only, a row that carries one is refused on import: it is already in the system. With Add new and update existing, it names the lead to update.",
  },
  {
    key: "createdAt",
    header: "Captured",
    kind: "text",
    readOnly: true,
    help: "When the lead was captured. Filled in on export and ignored on import.",
  },
];

const OWNER = columnOf(COLUMNS, "owner");

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
    "When adding, leave Owner email blank and your organisation's round-robin rule assigns the lead. If your role limits you to your own leads, you can only name yourself.",
    "A lead whose email is already on file is skipped. Lead emails are matched exactly as typed: a lead already captured through the web form with the same address in different capitals will be added again, because the server matches leads on the exact email.",
    "A lead with no email cannot be checked, so importing the same file twice adds those leads twice.",
    "Phone numbers need a country code, like +91 98765 43210. A number without one is refused and reported.",
    "Exports show email and phone masked, as the app does.",
    "With Add new records only, a file exported from the Leads list cannot be imported again as it is: its rows carry a Lead ID and are refused, because those leads already exist and their masked emails could not be checked for duplicates.",
    "When updating, a row with a Lead ID changes that lead, and a lead can only be found that way: its email is hidden, so there is nothing else to find it by. A row without a Lead ID is a new lead, as when adding.",
    "When updating, Full name, Company, Owner email and Notes can change. Type [clear] in Company or Notes to empty it. Full name and Owner email cannot be emptied: a lead cannot be unassigned from a sheet.",
    "When updating, the Email and Phone of an existing lead cannot be changed, and the stored ones are hidden so they cannot be compared: a row that fills either in is refused. Leave them blank. Status and Source are not updated from a sheet either.",
    "When updating, a converted lead can still have its Full name, Company and Notes changed, but not its owner.",
  ],
  recordIdKey: "id",
  loadLookups: loadOwnerLookups,
  toPayload: (row, ctx) => {
    const owner = namedOwnerId(row, ctx);
    return {
      full_name: requiredTextOf(row, "fullName"),
      company: textOf(row, "company") ?? null,
      email: textOf(row, "email") ?? null,
      phone: textOf(row, "phone") ?? null,
      source: IMPORT_SOURCE,
      ...(owner !== undefined ? { owner_id: owner } : {}),
      notes: textOf(row, "notes") ?? "",
    };
  },
  create: (payload) => createLead(payload),
  update: {
    loadExisting: (ctx) => loadEvery(listLeads, ctx),
    idOf: (lead) => lead.id,
    fields: [
      { column: "fullName", field: "full_name", current: (lead) => lead.full_name },
      { column: "company", current: (lead) => orNull(lead.company), clear: null },
      {
        column: "owner",
        field: "owner_id",
        current: (lead) => lead.owner_id,
        resolve: resolveByLookup(OWNER, LOOKUP_ALL_OWNERS),
      },
      { column: "notes", current: (lead) => orNull(lead.notes), clear: "", same: sameMultiline },
      // Sealed on the server and never returned but as a mask: no cell can equal the stored value,
      // so any filled cell is refused (and `[clear]` with it), never sent and never ignored.
      { column: "email", current: (lead) => lead.email, immutable: true },
      { column: "phone", current: (lead) => lead.phone, immutable: true },
    ],
    // No endpoint updates either. Declared so the engine refuses a filled cell too, and so the
    // definition check knows they are left out on purpose.
    notUpdatable: ["email", "phone"],
    check: (row, lead, changed, ctx) => {
      refuseInactiveOwner(OWNER, row, changed, ctx);
      if (lead.status === "converted" && changed.includes(OWNER.header)) {
        throw new RowProblem(
          "A converted lead cannot be reassigned. Its Full name, Company and Notes can still be changed.",
        );
      }
    },
    send: async (lead, body) => {
      await updateLead(lead.id, patchOf<LeadPatch>(body));
    },
  },
  export: {
    fetchPage: ({ page, size, filters, signal }) =>
      listLeads({ status: filters.status, page, size, signal }),
    toRow: (lead, lookups) => ({
      fullName: lead.full_name,
      company: lead.company,
      email: null,
      phone: null,
      owner: labelFor(lookups[LOOKUP_ALL_OWNERS], lead.owner_id),
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
