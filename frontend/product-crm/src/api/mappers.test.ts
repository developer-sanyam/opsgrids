import { describe, expect, it } from "vitest";

import {
  activityFromWire,
  companyFromWire,
  contactFromWire,
  dealFromWire,
  mapPage,
  ownerFromWire,
} from "./mappers";

describe("companyFromWire", () => {
  it("maps a customer-service CompanyOut row onto this product's Company", () => {
    const wire = {
      id: "comp-1",
      name: "Northwind Logistics",
      domain: "northwind.co",
      industry: "Logistics",
      size_band: "501-1000",
      city: "Mumbai",
      country: "India",
      status: "customer" as const,
      owner_id: "own-1",
      annual_revenue_minor: 840000000000,
      currency: "INR",
      created_at: "2024-02-11T09:00:00Z",
    };
    expect(companyFromWire(wire)).toEqual(wire);
  });
});

describe("contactFromWire", () => {
  it("maps a ContactOut row 1:1", () => {
    const wire = {
      id: "cont-1",
      full_name: "Asha Rao",
      title: "VP Ops",
      email: "asha@northwind.co",
      phone: "+91 •• •••• ••12",
      company_id: "comp-1",
      status: "customer" as const,
      owner_id: "own-1",
      last_activity_at: null,
      created_at: "2024-02-12T09:00:00Z",
    };
    expect(contactFromWire(wire)).toEqual(wire);
  });
});

describe("dealFromWire", () => {
  it("maps a DealOut row 1:1", () => {
    const wire = {
      id: "deal-1",
      name: "Northwind renewal",
      company_id: "comp-1",
      primary_contact_id: "cont-1",
      value_minor: 500000000,
      currency: "INR",
      stage: "proposal" as const,
      owner_id: "own-1",
      probability: 60,
      close_date: "2026-10-01",
      created_at: "2026-08-01T09:00:00Z",
      last_activity_at: "2026-09-01T09:00:00Z",
      source: "referral",
    };
    expect(dealFromWire(wire)).toEqual(wire);
  });
});

describe("activityFromWire", () => {
  it("maps an ActivityOut row 1:1", () => {
    const wire = {
      id: "act-1",
      type: "call" as const,
      subject: "Renewal check-in",
      body: null,
      deal_id: "deal-1",
      company_id: "comp-1",
      contact_id: "cont-1",
      owner_id: "own-1",
      occurred_at: "2026-09-01T09:00:00Z",
      direction: "outbound" as const,
    };
    expect(activityFromWire(wire)).toEqual(wire);
  });
});

describe("ownerFromWire", () => {
  it("maps an OwnerOut row 1:1 — the rep directory GET /customers/owners returns", () => {
    const wire = { id: "own-1", full_name: "Sofia Marchetti", email: "sofia@example.com", role: "AE" };
    expect(ownerFromWire(wire)).toEqual(wire);
  });
});

describe("mapPage", () => {
  it("maps items and keeps total, dropping the gateway's page/size/pages fields", () => {
    const page = { items: [1, 2, 3], total: 3, page: 1, size: 200, pages: 1 };
    expect(mapPage(page, (n: number) => n * 2)).toEqual({ items: [2, 4, 6], total: 3 });
  });

  it("keeps total even when the current page is a narrower slice", () => {
    const page = { items: [1], total: 50, page: 1, size: 1, pages: 50 };
    expect(mapPage(page, (n: number) => n)).toEqual({ items: [1], total: 50 });
  });
});
