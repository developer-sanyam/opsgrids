/**
 * The 360 view never fetches and never fails whole: a section the service could not read says
 * "Not available" on its own line, a section it has never heard of is still shown by name, a
 * contact with no email has no email line, and money is formatted in each amount's own currency.
 */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Customer360View } from "./Customer360View";
import type { Customer360ViewModel } from "./Customer360View";

const LABELS: Record<string, string> = { crm: "CRM", orders: "Orders", finance: "Finance", support: "Support" };
const sectionLabel = (name: string) => LABELS[name] ?? `${name.charAt(0).toUpperCase()}${name.slice(1)}`;

const DATA: Customer360ViewModel = {
  customer: {
    id: "c-1",
    name: "Priya Raghavan",
    company: "Vaayu Foods",
    since: "2025-03-14T00:00:00Z",
    status: "active",
    tags: ["Wholesale"],
    owner: { id: "m-1", name: "Asha Menon" },
    phone: "•••••• 4210",
    email: "p•••••@vaayu.example.com",
    city: "Bengaluru",
  },
  crm: { stage: "Negotiation", open_opportunities: 2, hot: true },
  orders: {
    count_90d: 5,
    total_90d: { amount_minor: 1_250_000, currency: "USD" },
    latest: null,
  },
  finance: {
    outstanding: { amount_minor: 480_000, currency: "INR" },
    overdue_count: 1,
    latest_invoice: null,
  },
  support: { open_tickets: 1, total_tickets: 4 },
  channels: { whatsapp: null, email: null },
  timeline: [],
  unavailable: [],
};

function row(name: string): HTMLElement {
  const item = screen.getByRole("heading", { name, level: 3 }).closest("li");
  if (item === null) throw new Error(`no row for ${name}`);
  return item;
}

describe("Customer360View", () => {
  it("shows the profile: name, status, place, owner, tags and the masked contact lines", () => {
    render(<Customer360View data={DATA} sectionLabel={sectionLabel} />);
    expect(screen.getByRole("heading", { name: "Priya Raghavan" })).toBeTruthy();
    expect(screen.getByText("Active")).toBeTruthy();
    expect(screen.getByText("Vaayu Foods · Bengaluru")).toBeTruthy();
    expect(screen.getByText("Asha Menon")).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "Tags" })).getByText("Wholesale")).toBeTruthy();
    expect(screen.getByText("•••••• 4210")).toBeTruthy();
    expect(screen.queryByRole("link", { name: /4210/ })).toBeNull();
  });

  it("null_and_unavailable_sections_say_Not_available", () => {
    render(
      <Customer360View
        data={{ ...DATA, orders: null, unavailable: ["support"] }}
        sectionLabel={sectionLabel}
        sectionHref={(name) => `https://example.test/${name}`}
      />,
    );
    expect(within(row("Orders")).getByText("Not available")).toBeTruthy();
    expect(within(row("Support")).getByText("Not available")).toBeTruthy();
    expect(within(row("CRM")).getByText(/Stage: Negotiation · 2 open opportunities/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "View Support records" })).toBeNull();
    expect(screen.getByRole("link", { name: "View CRM records" }).getAttribute("href")).toBe("https://example.test/crm");
  });

  it("an_unknown_section_name_is_rendered_not_dropped", () => {
    render(<Customer360View data={{ ...DATA, unavailable: ["delivery"] }} sectionLabel={sectionLabel} />);
    expect(within(row("Delivery")).getByText("Not available")).toBeTruthy();
  });

  it("phone_only_profile_has_no_email_line, and the reveal slot sits under the phone", () => {
    render(
      <Customer360View
        data={{ ...DATA, customer: { ...DATA.customer, email: null } }}
        sectionLabel={sectionLabel}
        revealSlot={<button type="button">Reveal phone number</button>}
      />,
    );
    expect(screen.queryByText("Email")).toBeNull();
    expect(screen.getByText("Phone")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reveal phone number" })).toBeTruthy();
  });

  it("money_is_per_currency", () => {
    render(<Customer360View data={DATA} sectionLabel={sectionLabel} />);
    expect(within(row("Orders")).getByText(/5 orders in the last 90 days · \$12,500/)).toBeTruthy();
    expect(within(row("Finance")).getByText(/₹4,800 outstanding · 1 overdue invoice/)).toBeTruthy();
  });
});
