/**
 * Companies from and to a spreadsheet (ADR-0022). Import calls `POST /customers` once per row, so
 * the server keeps every rule: the name is unique per organisation (a repeat is a 409 and is
 * skipped), and a caller limited to their own book may only assign companies to themselves.
 *
 * Update calls `PATCH /customers/{id}` with only the fields that differ. The name is the list's
 * unique key, and the server compares it ignoring capitals and surrounding spaces (never spaces
 * inside it), so a row with no Company ID finds its company that way (`keyMatch: "caseless"`, the
 * one list where that is the server's own rule); a rename therefore needs the Company ID, or the new
 * name would look like a new company. Every importable column can change, five can be emptied with
 * `[clear]` (the endpoint takes `""` there and refuses `null` everywhere), and the amount is never
 * converted when the currency changes.
 */
import { defineImportEntity, minorToMajor } from "@opsgrids/client";
import type { ColumnOption, ImportColumn } from "@opsgrids/client";

import { createCompany, listCompanies, updateCompany } from "@/api/client";
import type { Company, CompanyStatus } from "@/api/types";
import type { CompanyIn, CompanyPatch } from "@/api/wire";

import {
  LOOKUP_ALL_OWNERS,
  LOOKUP_OWNERS,
  OWNER_HINT,
  OWNER_HINT_UPDATE,
  currencyOf,
  labelFor,
  loadOwnerLookups,
  numberOf,
  optionLabel,
  optionOf,
  ownerIdOrSelf,
  requiredTextOf,
  textOf,
} from "./shared";
import {
  columnOf,
  loadEvery,
  orNull,
  patchOf,
  refuseCurrencyChangeWithoutAmount,
  refuseInactiveOwner,
  resolveByLookup,
  upperCased,
} from "./updateShared";

/** What the Companies screen filters its list by; the export writes exactly that list. */
export interface CompanyFilters {
  search: string;
  status: CompanyStatus | "all";
}

const STATUS_OPTIONS: readonly (ColumnOption & { value: CompanyStatus })[] = [
  { value: "prospect", label: "Prospect" },
  { value: "customer", label: "Customer" },
  { value: "lead", label: "Lead" },
  { value: "churned", label: "Churned" },
];

const COLUMNS: readonly ImportColumn[] = [
  {
    key: "name",
    header: "Company name",
    kind: "text",
    required: true,
    maxLength: 200,
    help: "The company's name. When adding, it must be new: a company with the same name (ignoring capitals) is skipped. When updating, a row with no Company ID finds its company by this name.",
  },
  {
    key: "owner",
    header: "Owner email",
    kind: "text",
    lookup: LOOKUP_OWNERS,
    lookupHint: OWNER_HINT,
    lookupHintUpdate: OWNER_HINT_UPDATE,
    help: "Email of the team member who owns the company, from the Lists sheet. Leave blank to own it yourself, or, when updating, to keep the current owner.",
  },
  {
    key: "domain",
    header: "Domain",
    kind: "text",
    maxLength: 120,
    help: "The company's website domain, like acme.com.",
  },
  {
    key: "industry",
    header: "Industry",
    kind: "text",
    maxLength: 80,
    help: "The industry it works in, like Logistics.",
  },
  {
    key: "sizeBand",
    header: "Headcount",
    kind: "text",
    maxLength: 20,
    help: "Headcount band: 1-10, 11-50, 51-200, 201-500, 501-1000 or 1000+.",
  },
  {
    key: "city",
    header: "City",
    kind: "text",
    maxLength: 120,
    help: "The city its head office is in.",
  },
  {
    key: "country",
    header: "Country",
    kind: "text",
    maxLength: 120,
    help: "The country its head office is in.",
  },
  {
    key: "status",
    header: "Status",
    kind: "enum",
    options: STATUS_OPTIONS,
    help: "Where the company stands with you. Leave blank for Prospect, or, when updating, to keep the current status.",
  },
  {
    key: "annualRevenue",
    header: "Annual revenue",
    kind: "money",
    min: 0,
    currencyKey: "currency",
    help: "Yearly revenue in whole currency units, like 84000000 or 84000000.50. Leave blank if unknown, or, when updating, to keep the current figure.",
  },
  {
    key: "currency",
    header: "Currency",
    kind: "text",
    maxLength: 3,
    pattern: /^[A-Za-z]{3}$/,
    patternHint: "Three letters, like INR or USD.",
    help: "Currency of the annual revenue, as a three-letter code. Leave blank for INR, or, when updating, to keep the company's own currency.",
  },
  {
    key: "id",
    header: "Company ID",
    kind: "text",
    readOnly: true,
    help: "The record's id. Filled in on export. With Add new records only, a row that carries one is refused on import: it is already in the system. With Add new and update existing, it names the company to update.",
  },
  {
    key: "createdAt",
    header: "Created",
    kind: "text",
    readOnly: true,
    help: "When the company was added. Filled in on export and ignored on import.",
  },
];

const OWNER = columnOf(COLUMNS, "owner");
const AMOUNT = columnOf(COLUMNS, "annualRevenue");
const CURRENCY = columnOf(COLUMNS, "currency");

export const companyImport = defineImportEntity<CompanyIn, Company, CompanyFilters>({
  id: "crm.companies",
  noun: "companies",
  nounSingular: "company",
  fileStem: "companies",
  columns: COLUMNS,
  sampleRows: [
    {
      name: "Northwind Logistics",
      domain: "northwind.co",
      industry: "Logistics",
      sizeBand: "501-1000",
      city: "Mumbai",
      country: "India",
      status: "Customer",
      annualRevenue: 84000000,
      currency: "INR",
    },
    {
      name: "Cobalt Financial",
      domain: "cobaltfin.com",
      industry: "Financial Services",
      sizeBand: "201-500",
      city: "Bengaluru",
      country: "India",
      status: "Prospect",
      annualRevenue: 56000000,
      currency: "INR",
    },
  ],
  notes: [
    "With Add new records only, a company whose name already exists (ignoring capitals) is skipped, not updated.",
    "When adding, leave Owner email blank to own the company yourself.",
    "If your role limits you to your own companies, you can only assign companies to yourself. A row naming another owner is refused and reported.",
    "Annual revenue is in whole currency units and uses the currency in the next column. When adding, leave Currency blank for INR.",
    "With Add new records only, a file exported from the Companies list cannot be imported again as it is: its rows carry a Company ID and are refused, because those companies already exist.",
    "When updating, a row with a Company ID changes that company. Without one, the company is found by its name, ignoring capitals and surrounding spaces (spaces inside the name count). To rename a company keep its Company ID in the row: a row with a changed name and no Company ID is a new company.",
    "A rename onto the name of another company (ignoring capitals) is refused by the server and reported. A change of capitals alone is a rename and is applied.",
    "When updating, type [clear] in Domain, Industry, Headcount, City or Country to empty it. Company name, Owner email, Status, Annual revenue and Currency cannot be emptied, only changed.",
    "When updating, Annual revenue is not converted if the currency changes: change Currency only together with Annual revenue, in the new currency. Leave Currency blank to keep the company's own currency; Annual revenue is then read in it, not in INR.",
  ],
  recordIdKey: "id",
  loadLookups: loadOwnerLookups,
  toPayload: (row, ctx) => ({
    name: requiredTextOf(row, "name"),
    owner_id: ownerIdOrSelf(row, ctx),
    domain: textOf(row, "domain") ?? "",
    industry: textOf(row, "industry") ?? "",
    size_band: textOf(row, "sizeBand") ?? "",
    city: textOf(row, "city") ?? "",
    country: textOf(row, "country") ?? "",
    status: optionOf(row, "status", STATUS_OPTIONS) ?? "prospect",
    annual_revenue_minor: numberOf(row, "annualRevenue") ?? 0,
    currency: currencyOf(row),
  }),
  create: (payload) => createCompany(payload),
  update: {
    loadExisting: (ctx) => loadEvery(listCompanies, ctx),
    idOf: (company) => company.id,
    keyColumn: "name",
    keyOf: (company) => company.name,
    // The server folds capitals and trims when it compares company names, so a name in other
    // capitals is the same company (the other three lists compare exactly).
    keyMatch: "caseless",
    recordLabel: (company) => company.name,
    currencyOf: (company) => company.currency,
    fields: [
      { column: "name", current: (company) => company.name },
      {
        column: "owner",
        field: "owner_id",
        current: (company) => company.owner_id,
        resolve: resolveByLookup(OWNER, LOOKUP_ALL_OWNERS),
      },
      { column: "domain", current: (company) => orNull(company.domain), clear: "" },
      { column: "industry", current: (company) => orNull(company.industry), clear: "" },
      { column: "sizeBand", field: "size_band", current: (company) => orNull(company.size_band), clear: "" },
      { column: "city", current: (company) => orNull(company.city), clear: "" },
      { column: "country", current: (company) => orNull(company.country), clear: "" },
      { column: "status", current: (company) => company.status },
      { column: "annualRevenue", field: "annual_revenue_minor", current: (company) => company.annual_revenue_minor },
      { column: "currency", current: (company) => company.currency, resolve: upperCased },
    ],
    check: (row, company, changed, ctx) => {
      refuseInactiveOwner(OWNER, row, changed, ctx);
      refuseCurrencyChangeWithoutAmount(
        { amount: AMOUNT, currency: CURRENCY },
        row,
        changed,
        company.annual_revenue_minor,
      );
    },
    send: async (company, body) => {
      await updateCompany(company.id, patchOf<CompanyPatch>(body));
    },
  },
  export: {
    fetchPage: ({ page, size, filters, signal }) =>
      listCompanies({ search: filters.search, status: filters.status, page, size, signal }),
    toRow: (company, lookups) => ({
      name: company.name,
      owner: labelFor(lookups[LOOKUP_ALL_OWNERS], company.owner_id),
      domain: company.domain,
      industry: company.industry,
      sizeBand: company.size_band,
      city: company.city,
      country: company.country,
      status: optionLabel(STATUS_OPTIONS, company.status),
      annualRevenue: minorToMajor(company.annual_revenue_minor, company.currency),
      currency: company.currency,
      id: company.id,
      createdAt: company.created_at,
    }),
  },
});
