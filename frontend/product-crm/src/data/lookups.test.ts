import { describe, expect, it } from "vitest";

import type { Company, Contact, Owner } from "@/api/types";
import { companyById, companyName, contactById, contactName, contactsForCompany, ownerById, ownerName } from "./lookups";

const OWNERS: Owner[] = [
  { id: "own-1", full_name: "Sofia Marchetti", email: "sofia@example.com", role: "AE" },
];

const COMPANIES: Company[] = [
  {
    id: "comp-1",
    name: "Northwind Logistics",
    domain: "northwind.co",
    industry: "Logistics",
    size_band: "501-1000",
    city: "Mumbai",
    country: "India",
    status: "customer",
    owner_id: "own-1",
    annual_revenue_minor: 840000000000,
    currency: "INR",
    created_at: "2024-02-11T09:00:00Z",
  },
];

const CONTACTS: Contact[] = [
  {
    id: "cont-1",
    full_name: "Asha Rao",
    title: "VP Ops",
    email: "asha@northwind.co",
    phone: "+91 •• •••• ••12",
    company_id: "comp-1",
    status: "customer",
    owner_id: "own-1",
    last_activity_at: null,
    created_at: "2024-02-12T09:00:00Z",
  },
  {
    id: "cont-2",
    full_name: "Rohit Verma",
    title: "Procurement Lead",
    email: "rohit@northwind.co",
    phone: "+91 •• •••• ••34",
    company_id: "comp-1",
    status: "customer",
    owner_id: "own-1",
    last_activity_at: null,
    created_at: "2024-03-01T09:00:00Z",
  },
];

describe("ownerById / ownerName", () => {
  it("resolves a known id", () => {
    expect(ownerById(OWNERS, "own-1")).toEqual(OWNERS[0]);
    expect(ownerName(OWNERS, "own-1")).toBe("Sofia Marchetti");
  });

  it("degrades to null / 'Unassigned' for an id not (yet) in the directory — never throws", () => {
    expect(ownerById(OWNERS, "own-missing")).toBeNull();
    expect(ownerName(OWNERS, "own-missing")).toBe("Unassigned");
    expect(ownerName([], "own-1")).toBe("Unassigned");
  });

  it("treats a null id as unassigned", () => {
    expect(ownerById(OWNERS, null)).toBeNull();
    expect(ownerName(OWNERS, null)).toBe("Unassigned");
  });
});

describe("companyById / companyName", () => {
  it("resolves a known id", () => {
    expect(companyById(COMPANIES, "comp-1")).toEqual(COMPANIES[0]);
    expect(companyName(COMPANIES, "comp-1")).toBe("Northwind Logistics");
  });

  it("degrades to null / an em dash while the directory hasn't loaded that row", () => {
    expect(companyById([], "comp-1")).toBeNull();
    expect(companyName([], "comp-1")).toBe("—");
  });
});

describe("contactById / contactName", () => {
  it("resolves a known id", () => {
    expect(contactById(CONTACTS, "cont-1")).toEqual(CONTACTS[0]);
    expect(contactName(CONTACTS, "cont-1")).toBe("Asha Rao");
  });

  it("degrades to null / an em dash for an unknown id", () => {
    expect(contactById(CONTACTS, "cont-missing")).toBeNull();
    expect(contactName(CONTACTS, "cont-missing")).toBe("—");
  });
});

describe("contactsForCompany", () => {
  it("filters the whole directory to one company's contacts — the contacts list has no server-side filter for it", () => {
    expect(contactsForCompany(CONTACTS, "comp-1")).toEqual(CONTACTS);
    expect(contactsForCompany(CONTACTS, "comp-missing")).toEqual([]);
  });
});
