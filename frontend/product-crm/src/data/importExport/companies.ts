/**
 * Companies from and to a spreadsheet (ADR-0022). Import calls `POST /customers` once per row, so
 * the server keeps every rule: the name is unique per organisation (a repeat is a 409 and is
 * skipped), and a caller limited to their own book may only assign companies to themselves.
 */
import { defineImportEntity } from "@opsgrids/client";
import type { ColumnOption, ImportColumn } from "@opsgrids/client";

import { createCompany, listCompanies } from "@/api/client";
import type { Company, CompanyStatus } from "@/api/types";
import type { CompanyIn } from "@/api/wire";

import {
  LOOKUP_OWNERS,
  currencyOf,
  loadOwnerLookup,
  minorToMajor,
  numberOf,
  optionLabel,
  optionOf,
  ownerEmail,
  ownerIdOrSelf,
  requiredTextOf,
  textOf,
} from "./shared";

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
    help: "The company's name. It must be new: a company with the same name (ignoring capitals) is skipped.",
  },
  {
    key: "owner",
    header: "Owner email",
    kind: "text",
    lookup: LOOKUP_OWNERS,
    help: "Email of the team member who owns the company, from the Lists sheet. Leave blank to own it yourself.",
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
    help: "Where the company stands with you. Leave blank for Prospect.",
  },
  {
    key: "annualRevenue",
    header: "Annual revenue",
    kind: "money",
    min: 0,
    currencyKey: "currency",
    help: "Yearly revenue in whole currency units, like 84000000 or 84000000.50. Leave blank if unknown.",
  },
  {
    key: "currency",
    header: "Currency",
    kind: "text",
    maxLength: 3,
    pattern: /^[A-Za-z]{3}$/,
    patternHint: "Three letters, like INR or USD.",
    help: "Currency of the annual revenue, as a three-letter code. Leave blank for INR.",
  },
  {
    key: "id",
    header: "Company ID",
    kind: "text",
    readOnly: true,
    help: "The record's id. Filled in on export and ignored on import.",
  },
  {
    key: "createdAt",
    header: "Created",
    kind: "text",
    readOnly: true,
    help: "When the company was added. Filled in on export and ignored on import.",
  },
];

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
    "Import only adds companies. A company whose name already exists (ignoring capitals) is skipped, not updated.",
    "Leave Owner email blank to own the company yourself.",
    "If your role limits you to your own companies, you can only assign companies to yourself. A row naming another owner is refused and reported.",
    "Annual revenue is in whole currency units and uses the currency in the next column. Leave Currency blank for INR.",
  ],
  loadLookups: async (signal) => ({ [LOOKUP_OWNERS]: await loadOwnerLookup(signal) }),
  toPayload: (row, ctx) => ({
    name: requiredTextOf(row, "name", "Company name"),
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
  export: {
    fetchPage: ({ page, size, filters, signal }) =>
      listCompanies({ search: filters.search, status: filters.status, page, size, signal }),
    toRow: (company, lookups) => ({
      name: company.name,
      owner: ownerEmail(lookups[LOOKUP_OWNERS], company.owner_id),
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
