/**
 * Deals from and to a spreadsheet (ADR-0022). Import calls `POST /deals` once per row.
 *
 * The deal service cannot see the customer schema, so it does not check that the company or the
 * primary contact exists: a wrong id would be stored as an orphan. Both are therefore resolved
 * from the full lists before anything is sent, and a primary contact must belong to the row's
 * company (the "New deal" form offers only that company's contacts).
 *
 * Deal names are unique across the whole organisation, not per company, and the server compares
 * them exactly, capitals included; a repeat is a 409 and is skipped.
 */
import { RowProblem, defineImportEntity, minorToMajor } from "@opsgrids/client";
import type { ColumnOption, ImportColumn } from "@opsgrids/client";

import { createDeal, listDeals } from "@/api/client";
import type { Deal, DealStage } from "@/api/types";
import type { DealIn } from "@/api/wire";
import { STAGES } from "@/data/stages";

import {
  COMPANY_HINT,
  CONTACT_HINT,
  LOOKUP_ALL_OWNERS,
  LOOKUP_COMPANIES,
  LOOKUP_CONTACTS,
  LOOKUP_OWNERS,
  OWNER_HINT,
  companyIdByName,
  contactRefOf,
  currencyOf,
  labelFor,
  loadCompanyLookup,
  loadContactLookups,
  loadOwnerLookups,
  numberOf,
  optionLabel,
  optionOf,
  ownerIdOrSelf,
  requiredNumberOf,
  requiredTextOf,
  textOf,
} from "./shared";

/** What the Deals screen filters its list by; the export writes exactly that list, not the page
 *  or the board column on screen. */
export interface DealFilters {
  search: string;
  stage: DealStage | "all";
  ownerId: string | "all";
}

const STAGE_OPTIONS: readonly (ColumnOption & { value: DealStage })[] = STAGES.map((s) => ({
  value: s.stage,
  label: s.label,
}));

const COLUMNS: readonly ImportColumn[] = [
  {
    key: "name",
    header: "Deal name",
    kind: "text",
    required: true,
    maxLength: 200,
    help: "The deal's name. Names are unique across the whole organisation and matched exactly, capitals included: a deal with the same exact name is skipped, while 'Q4 Renewal' and 'Q4 renewal' are different deals to the server.",
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
    key: "contact",
    header: "Primary contact email",
    kind: "text",
    lookup: LOOKUP_CONTACTS,
    lookupHint: CONTACT_HINT,
    help: "Email of the deal's main contact, from the Lists sheet. They must be a contact of the company. Leave blank for none.",
  },
  {
    key: "owner",
    header: "Owner email",
    kind: "text",
    lookup: LOOKUP_OWNERS,
    lookupHint: OWNER_HINT,
    help: "Email of the team member who owns the deal, from the Lists sheet. Leave blank to own it yourself.",
  },
  {
    key: "value",
    header: "Deal value",
    kind: "money",
    required: true,
    min: 0,
    currencyKey: "currency",
    help: "The deal's value in whole currency units, like 3200000 or 3200000.50.",
  },
  {
    key: "currency",
    header: "Currency",
    kind: "text",
    maxLength: 3,
    pattern: /^[A-Za-z]{3}$/,
    patternHint: "Three letters, like INR or USD.",
    help: "Currency of the deal value, as a three-letter code. Leave blank for INR.",
  },
  {
    key: "closeDate",
    header: "Close date",
    kind: "date",
    required: true,
    help: "The expected close date, like 2026-12-31.",
  },
  {
    key: "stage",
    header: "Stage",
    kind: "enum",
    options: STAGE_OPTIONS,
    help: "The pipeline stage the deal is in. Leave blank for Lead.",
  },
  {
    key: "probability",
    header: "Probability (%)",
    kind: "integer",
    min: 0,
    max: 100,
    help: "Chance of winning, as a whole number from 0 to 100. Leave blank to use the stage's usual figure.",
  },
  {
    key: "source",
    header: "Source",
    kind: "text",
    maxLength: 60,
    help: "Where the deal came from, like Referral, Outbound or Inbound.",
  },
  {
    key: "lastActivity",
    header: "Last activity",
    kind: "text",
    readOnly: true,
    help: "When the deal was last logged against. Filled in on export and ignored on import.",
  },
  {
    key: "id",
    header: "Deal ID",
    kind: "text",
    readOnly: true,
    help: "The record's id. Filled in on export. A row that carries one is refused on import: it is already in the system.",
  },
  {
    key: "createdAt",
    header: "Created",
    kind: "text",
    readOnly: true,
    help: "When the deal was opened. Filled in on export and ignored on import.",
  },
];

export const dealImport = defineImportEntity<DealIn, Deal, DealFilters>({
  id: "crm.deals",
  noun: "deals",
  nounSingular: "deal",
  fileStem: "deals",
  columns: COLUMNS,
  // No example names a primary contact. The sample sheet swaps an example company or contact the
  // organisation lacks for the first one it has, each on its own, so a contact could end up beside
  // a company it does not belong to and the example would fail its own check.
  sampleRows: [
    {
      name: "Northwind fleet renewal",
      company: "Northwind Logistics",
      value: 3200000,
      currency: "INR",
      closeDate: "2026-12-15",
      stage: "Proposal",
      probability: 55,
      source: "Referral",
    },
    {
      name: "Cobalt treasury platform",
      company: "Cobalt Financial",
      value: 1850000,
      currency: "INR",
      closeDate: "2027-01-31",
      stage: "Qualified",
      source: "Outbound",
    },
  ],
  notes: [
    "Deal names must be unique across the whole organisation, not just within a company. Names are matched exactly, capitals included: a deal whose exact name already exists is skipped, not updated, while 'Q4 Renewal' and 'Q4 renewal' are different deals to the server.",
    "The company must already exist. If it is new, import it on the Companies screen first.",
    "A primary contact must be a contact of that company. Leave the column blank for none.",
    "Leave Owner email blank to own the deal yourself. Assigning a deal to someone else needs organisation-wide access; otherwise that row is refused and reported.",
    "Deal value is in whole currency units and uses the currency in the next column. Leave Currency blank for INR.",
    "Leave Stage blank for Lead, and Probability blank to use the stage's usual figure.",
    "A file exported from the Deals list cannot be imported again as it is: its rows carry a Deal ID and are refused, because those deals already exist.",
  ],
  recordIdKey: "id",
  loadLookups: async (signal) => {
    const [owners, companies, contacts] = await Promise.all([
      loadOwnerLookups(signal),
      loadCompanyLookup(signal),
      loadContactLookups(signal),
    ]);
    return { ...owners, [LOOKUP_COMPANIES]: companies, ...contacts };
  },
  toPayload: (row, ctx) => {
    const company = requiredTextOf(row, "company");
    const companyId = companyIdByName(company, ctx);
    const contactCell = textOf(row, "contact");
    let primaryContactId: string | undefined;
    if (contactCell !== undefined) {
      const contact = contactRefOf(contactCell, ctx);
      if (contact.companyId !== companyId) {
        throw new RowProblem(
          `${contactCell} is not a contact of ${company}. Pick one of that company's contacts.`,
        );
      }
      primaryContactId = contact.contactId;
    }
    const probability = numberOf(row, "probability");
    return {
      name: requiredTextOf(row, "name"),
      company_id: companyId,
      owner_id: ownerIdOrSelf(row, ctx),
      value_minor: requiredNumberOf(row, "value"),
      close_date: requiredTextOf(row, "closeDate"),
      currency: currencyOf(row),
      stage: optionOf(row, "stage", STAGE_OPTIONS) ?? "lead",
      ...(probability !== undefined ? { probability } : {}),
      ...(primaryContactId !== undefined ? { primary_contact_id: primaryContactId } : {}),
      source: textOf(row, "source") ?? "",
    };
  },
  create: (payload) => createDeal(payload),
  export: {
    fetchPage: ({ page, size, filters, signal }) =>
      listDeals({
        search: filters.search,
        stage: filters.stage,
        ownerId: filters.ownerId,
        page,
        size,
        signal,
      }),
    toRow: (deal, lookups) => ({
      name: deal.name,
      company: labelFor(lookups[LOOKUP_COMPANIES], deal.company_id),
      contact: labelFor(lookups[LOOKUP_CONTACTS], deal.primary_contact_id),
      owner: labelFor(lookups[LOOKUP_ALL_OWNERS], deal.owner_id),
      value: minorToMajor(deal.value_minor, deal.currency),
      currency: deal.currency,
      closeDate: deal.close_date,
      stage: optionLabel(STAGE_OPTIONS, deal.stage),
      probability: deal.probability,
      source: deal.source,
      lastActivity: deal.last_activity_at,
      id: deal.id,
      createdAt: deal.created_at,
    }),
  },
});
