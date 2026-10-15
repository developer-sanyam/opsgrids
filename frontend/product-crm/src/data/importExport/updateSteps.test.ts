/**
 * One request per step. The runner hands each step of an update row (the field update, then each
 * extra step) to its pacing and its 429 wait, and tries a rate-limited step again on its own: a 429
 * request never ran, but the steps before it did, and repeating those would repeat a write. That
 * only holds if every step is exactly one HTTP request, so this runs each kind of row through the
 * engine's own step hook and counts the requests each step made.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ParsedValue, UpdatePlan } from "@opsgrids/client";

import type { Company, Contact, Deal, Lead } from "@/api/types";
import { companyImport } from "./companies";
import { contactImport } from "./contacts";
import { dealImport } from "./deals";
import { companyWire, contactWire, contextWith, dealWire, leadWire, present, stubGateway } from "./entityTestKit";
import type { Recorded } from "./entityTestKit";
import { leadImport } from "./leads";
import { OWNER_LOOKUPS, planFor, requestLines } from "./updateTestKit";

const ctx = contextWith(OWNER_LOOKUPS);

afterEach(() => {
  vi.unstubAllGlobals();
});

const COMPANY: Company = {
  id: "comp-1",
  name: "Northwind Logistics",
  domain: "northwind.co",
  industry: "Logistics",
  size_band: "",
  city: "Mumbai",
  country: "India",
  status: "customer",
  owner_id: "own-2",
  annual_revenue_minor: 100,
  currency: "INR",
  created_at: "2024-02-11T09:00:00Z",
};
const CONTACT: Contact = {
  id: "cont-1",
  full_name: "Asha Rao",
  title: "VP Operations",
  email: "asha.rao@northwind.co",
  phone: "+•• •••• ••210",
  company_id: "comp-1",
  status: "customer",
  owner_id: "own-2",
  last_activity_at: null,
  created_at: "2024-02-12T09:00:00Z",
};
const DEAL: Deal = {
  id: "deal-1",
  name: "Northwind fleet renewal",
  company_id: "comp-1",
  primary_contact_id: null,
  value_minor: 100,
  currency: "INR",
  stage: "proposal",
  owner_id: "own-2",
  probability: 55,
  close_date: "2026-12-15",
  created_at: "2026-09-27T09:00:00Z",
  last_activity_at: null,
  source: "",
};
const LEAD: Lead = {
  id: "lead-1",
  full_name: "Meera Iyer",
  company: null,
  email: null,
  phone: null,
  source: "import",
  status: "working",
  next_statuses: [],
  owner_id: null,
  notes: "",
  converted_company_id: null,
  converted_contact_id: null,
  converted_deal_id: null,
  converted_at: null,
  created_at: "2026-09-20T09:00:00Z",
};

/** How many requests each step of the plan made, in the order the steps ran. */
async function requestsPerStep(plan: UpdatePlan | null, calls: readonly Recorded[]): Promise<number[]> {
  const counts: number[] = [];
  await present(plan ?? undefined, "an update plan").run(async (step) => {
    const before = calls.length;
    await step();
    counts.push(calls.length - before);
  });
  return counts;
}

interface Scenario {
  what: string;
  /** The requests the row sends, in order. */
  requests: string[];
  steps: (calls: Recorded[]) => Promise<number[]>;
}

const cells = (values: Record<string, ParsedValue>) => values;

const SCENARIOS: readonly Scenario[] = [
  {
    what: "a company row that changes several fields",
    requests: ["PATCH /api/v1/customers/comp-1"],
    steps: (calls) => requestsPerStep(planFor(companyImport, COMPANY, cells({ city: "Pune", status: "prospect" }), ctx), calls),
  },
  {
    what: "a lead row that changes several fields",
    requests: ["PATCH /api/v1/leads/lead-1"],
    steps: (calls) =>
      requestsPerStep(planFor(leadImport, LEAD, cells({ fullName: "M. Iyer", owner: "sofia@example.com", notes: "Called." }), ctx), calls),
  },
  {
    what: "a contact row that changes fields and the phone",
    requests: ["PATCH /api/v1/contacts/cont-1", "PUT /api/v1/contacts/cont-1/phone"],
    steps: (calls) =>
      requestsPerStep(planFor(contactImport, CONTACT, cells({ title: "COO", status: "qualified", phone: "+919876543210" }), ctx), calls),
  },
  {
    what: "a contact row that changes only the phone",
    requests: ["PUT /api/v1/contacts/cont-1/phone"],
    steps: (calls) => requestsPerStep(planFor(contactImport, CONTACT, cells({ phone: "+919876543210" }), ctx), calls),
  },
  {
    what: "a deal row that changes fields and the stage",
    requests: ["PATCH /api/v1/deals/deal-1", "POST /api/v1/deals/deal-1/stage"],
    steps: (calls) =>
      requestsPerStep(planFor(dealImport, DEAL, cells({ value: 200, source: "Referral", stage: "negotiation" }), ctx), calls),
  },
  {
    what: "a deal row that changes only the stage and the probability",
    requests: ["POST /api/v1/deals/deal-1/stage"],
    steps: (calls) => requestsPerStep(planFor(dealImport, DEAL, cells({ stage: "closed_won", probability: 100 }), ctx), calls),
  },
  {
    what: "a deal row that changes fields and the probability without a stage move",
    requests: ["PATCH /api/v1/deals/deal-1", "PATCH /api/v1/deals/deal-1"],
    steps: (calls) => requestsPerStep(planFor(dealImport, DEAL, cells({ value: 200, probability: 70 }), ctx), calls),
  },
  {
    what: "a deal row that changes only the probability",
    requests: ["PATCH /api/v1/deals/deal-1"],
    steps: (calls) => requestsPerStep(planFor(dealImport, DEAL, cells({ probability: 70 }), ctx), calls),
  },
];

describe("update rows: every step is exactly one request", () => {
  it.each(SCENARIOS)("$what", async ({ requests, steps }) => {
    const calls = stubGateway(({ url }) => {
      if (url.pathname.includes("/customers")) return companyWire("comp-1", "x");
      if (url.pathname.includes("/contacts")) return contactWire("cont-1", "asha.rao@northwind.co", "comp-1");
      if (url.pathname.includes("/leads")) return leadWire("lead-1", "Meera Iyer");
      return dealWire("deal-1", "x");
    });
    expect(await steps(calls)).toEqual(requests.map(() => 1));
    expect(requestLines(calls)).toEqual(requests);
  });
});
