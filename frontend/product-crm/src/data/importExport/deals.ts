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
 *
 * Update calls `PATCH /deals/{id}` with only the fields that differ, and finds a deal with no Deal
 * ID by its name (exact, but only app-level unique: two deals with one name make the row ambiguous
 * and it is refused). The PATCH takes neither stage nor probability, which are extra steps after
 * it, one request each. A changed stage goes to `POST /deals/{id}/stage`, which resets the
 * probability to the new stage's usual figure unless it is given one: a probability the row changes
 * therefore travels with the stage move, and a probability changed with no stage move is a PATCH of
 * its own. The server checks none of owner, company and primary contact, so the row's are resolved
 * from the lists, and a contact must work at the deal's company, as when adding.
 */
import { CLEAR_TOKEN, RowProblem, defineImportEntity, minorToMajor } from "@opsgrids/client";
import type { ColumnOption, ImportColumn, ImportContext, ParsedRow } from "@opsgrids/client";

import { createDeal, listDeals, moveDealStage, updateDeal } from "@/api/client";
import type { Deal, DealStage } from "@/api/types";
import type { DealIn, DealPatch } from "@/api/wire";
import { STAGES, isOpen, stageLabel } from "@/data/stages";

import {
  COMPANY_HINT,
  COMPANY_HINT_UPDATE,
  CONTACT_HINT,
  CONTACT_HINT_UPDATE,
  LOOKUP_ALL_OWNERS,
  LOOKUP_COMPANIES,
  LOOKUP_COMPANY_OF_CONTACT,
  LOOKUP_CONTACTS,
  LOOKUP_OWNERS,
  OWNER_HINT,
  OWNER_HINT_UPDATE,
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
import {
  columnOf,
  loadEvery,
  lookupId,
  orNull,
  patchOf,
  refuseCurrencyChangeWithoutAmount,
  refuseInactiveOwner,
  resolveByLookup,
  upperCased,
} from "./updateShared";

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
    help: "The deal's name. Names are unique across the whole organisation and matched exactly, capitals included: a deal with the same exact name is skipped when adding, or found and updated when updating a row that has no Deal ID, while 'Q4 Renewal' and 'Q4 renewal' are different deals to the server.",
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
    key: "contact",
    header: "Primary contact email",
    kind: "text",
    lookup: LOOKUP_CONTACTS,
    lookupHint: CONTACT_HINT,
    lookupHintUpdate: CONTACT_HINT_UPDATE,
    help: "Email of the deal's main contact, from the Lists sheet. They must be a contact of the company. Leave blank for none, or, when updating, to keep the current one.",
  },
  {
    key: "owner",
    header: "Owner email",
    kind: "text",
    lookup: LOOKUP_OWNERS,
    lookupHint: OWNER_HINT,
    lookupHintUpdate: OWNER_HINT_UPDATE,
    help: "Email of the team member who owns the deal, from the Lists sheet. Leave blank to own it yourself, or, when updating, to keep the current owner.",
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
    help: "Currency of the deal value, as a three-letter code. Leave blank for INR, or, when updating, to keep the deal's own currency.",
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
    help: "The pipeline stage the deal is in. Leave blank for Lead, or, when updating, to keep the current stage.",
  },
  {
    key: "probability",
    header: "Probability (%)",
    kind: "integer",
    min: 0,
    max: 100,
    help: "Chance of winning, as a whole number from 0 to 100. Leave blank to use the stage's usual figure, or, when updating, to keep the current one.",
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
    help: "The record's id. Filled in on export. With Add new records only, a row that carries one is refused on import: it is already in the system. With Add new and update existing, it names the deal to update.",
  },
  {
    key: "createdAt",
    header: "Created",
    kind: "text",
    readOnly: true,
    help: "When the deal was opened. Filled in on export and ignored on import.",
  },
];

const COMPANY = columnOf(COLUMNS, "company");
const CONTACT = columnOf(COLUMNS, "contact");
const OWNER = columnOf(COLUMNS, "owner");
const VALUE = columnOf(COLUMNS, "value");
const CURRENCY = columnOf(COLUMNS, "currency");
const STAGE = columnOf(COLUMNS, "stage");
const PROBABILITY = columnOf(COLUMNS, "probability");

/**
 * The server does not check that a deal's primary contact works at its company, so an update that
 * changes either one is checked here, against what the deal will hold afterwards: the company the
 * row names (or the one on file), and the contact the row names (or, when it does not touch the
 * column, the one on file). A row that changes neither is not judged on what was already stored.
 * A contact the lists do not know (out of the caller's scope, or deleted) cannot be judged and stays.
 */
function contactMustWorkAtCompany(
  row: ParsedRow,
  deal: Deal,
  changed: readonly string[],
  ctx: ImportContext,
): void {
  const companyChanged = changed.includes(COMPANY.header);
  const contactChanged = changed.includes(CONTACT.header);
  if (!companyChanged && !contactChanged) return;

  const companyCell = textOf(row, "company");
  const contactCell = textOf(row, "contact");
  const companyId =
    companyChanged && companyCell !== undefined ? lookupId(COMPANY, companyCell, ctx) : deal.company_id;
  let contactId: string | null;
  if (contactCell !== undefined) contactId = lookupId(CONTACT, contactCell, ctx);
  else contactId = contactChanged ? null : deal.primary_contact_id; // a changed, blank cell was [clear]
  if (contactId === null) return;

  const worksAt = ctx.lookups[LOOKUP_COMPANY_OF_CONTACT]?.resolve(contactId);
  if (worksAt === undefined || worksAt === companyId) return;

  const company = companyCell ?? ctx.lookups[LOOKUP_COMPANIES]?.labelOf(deal.company_id) ?? "that company";
  if (contactCell !== undefined) {
    throw new RowProblem(`${contactCell} is not a contact of ${company}. Pick one of that company's contacts.`);
  }
  const email = ctx.lookups[LOOKUP_CONTACTS]?.labelOf(contactId) ?? "The primary contact";
  throw new RowProblem(
    `${email} is the deal's primary contact but is not a contact of ${company}. Name one of that company's contacts in ${CONTACT.header}, or type ${CLEAR_TOKEN} to remove the primary contact.`,
  );
}

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
    "Deal names must be unique across the whole organisation, not just within a company. Names are matched exactly, capitals included: with Add new records only, a deal whose exact name already exists is skipped, not updated, while 'Q4 Renewal' and 'Q4 renewal' are different deals to the server.",
    "The company must already exist. If it is new, import it on the Companies screen first.",
    "A primary contact must be a contact of that company. Leave the column blank for none.",
    "When adding, leave Owner email blank to own the deal yourself. Assigning a deal to someone else needs organisation-wide access; otherwise that row is refused and reported.",
    "Deal value is in whole currency units and uses the currency in the next column. When adding, leave Currency blank for INR.",
    "When adding, leave Stage blank for Lead, and Probability blank to use the stage's usual figure.",
    "With Add new records only, a file exported from the Deals list cannot be imported again as it is: its rows carry a Deal ID and are refused, because those deals already exist.",
    "When updating, a row with a Deal ID changes that deal. Without one, the deal is found by its exact name, capitals included. To rename a deal keep its Deal ID in the row: a row with a changed name and no Deal ID is a new deal. A rename onto another deal's exact name is refused by the server and reported. If two deals share a name, a row that names it without a Deal ID is refused.",
    "When updating, type [clear] in Primary contact email or Source to empty it. The other columns cannot be emptied, only changed.",
    "When updating, a changed Stage moves the deal through the pipeline and sets Probability to the new stage's usual figure, as dragging it on the board does. To set a different figure, change Probability in the same row. A deal that is Closed Won or Closed Lost cannot change stage; its other columns can still change.",
    "When updating, a Primary contact must be a contact of the deal's company. Changing Company keeps the deal's contact only if they work at the new company; otherwise name one of its contacts or type [clear].",
    "When updating, Deal value is not converted if the currency changes: change Currency only together with Deal value, in the new currency. Leave Currency blank to keep the deal's own currency; Deal value is then read in it, not in INR.",
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
  update: {
    loadExisting: (ctx) => loadEvery(listDeals, ctx),
    idOf: (deal) => deal.id,
    keyColumn: "name",
    keyOf: (deal) => deal.name,
    currencyOf: (deal) => deal.currency,
    fields: [
      { column: "name", current: (deal) => deal.name },
      {
        column: "company",
        field: "company_id",
        current: (deal) => deal.company_id,
        resolve: resolveByLookup(COMPANY),
      },
      {
        column: "contact",
        field: "primary_contact_id",
        current: (deal) => deal.primary_contact_id,
        resolve: resolveByLookup(CONTACT),
        clear: null,
      },
      {
        column: "owner",
        field: "owner_id",
        current: (deal) => deal.owner_id,
        resolve: resolveByLookup(OWNER, LOOKUP_ALL_OWNERS),
      },
      { column: "value", field: "value_minor", current: (deal) => deal.value_minor },
      { column: "currency", current: (deal) => deal.currency, resolve: upperCased },
      { column: "closeDate", field: "close_date", current: (deal) => deal.close_date },
      { column: "source", current: (deal) => orNull(deal.source), clear: "" },
    ],
    check: (row, deal, changed, ctx) => {
      refuseInactiveOwner(OWNER, row, changed, ctx);
      refuseCurrencyChangeWithoutAmount({ amount: VALUE, currency: CURRENCY }, row, changed, deal.value_minor);
      contactMustWorkAtCompany(row, deal, changed, ctx);
    },
    send: async (deal, body) => {
      await updateDeal(deal.id, patchOf<DealPatch>(body));
    },
    extraColumns: ["stage", "probability"],
    // After the PATCH, so a rename onto a taken name or an owner the caller may not assign is
    // refused before the deal has moved. Neither column can be emptied. A probability is sent
    // only when the row changes it: a row that repeats what is on file (an export always does) lets
    // a stage move set the new stage's usual figure, as dragging the card on the board does.
    extras: (row, cleared, deal) => {
      for (const column of [STAGE, PROBABILITY]) {
        if (cleared.has(column.key)) throw new RowProblem(`${column.header} cannot be cleared`);
      }
      const stage = optionOf(row, "stage", STAGE_OPTIONS);
      const named = numberOf(row, "probability");
      const probability = named !== undefined && named !== deal.probability ? named : undefined;
      if (stage !== undefined && stage !== deal.stage) {
        if (!isOpen(deal.stage)) {
          throw new RowProblem(
            `This deal is ${stageLabel(deal.stage)}, and a closed deal cannot be moved to another stage.`,
          );
        }
        return [
          {
            changed: probability === undefined ? [STAGE.header] : [STAGE.header, PROBABILITY.header],
            run: async () => {
              await moveDealStage(deal.id, { stage, ...(probability !== undefined ? { probability } : {}) });
            },
          },
        ];
      }
      if (probability === undefined) return [];
      return [
        {
          changed: [PROBABILITY.header],
          run: async () => {
            await updateDeal(deal.id, { probability });
          },
        },
      ];
    },
  },
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
