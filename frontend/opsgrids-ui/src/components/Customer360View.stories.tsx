import type { Meta, StoryObj } from "@storybook/react-vite";

import { Button } from "./Button";
import { Customer360View } from "./Customer360View";
import type { Customer360ViewModel } from "./Customer360View";

const LABELS: Record<string, string> = { crm: "CRM", orders: "Orders", finance: "Finance", support: "Support", delivery: "Delivery" };

const FULL: Customer360ViewModel = {
  customer: {
    id: "c-1",
    name: "Priya Raghavan",
    company: "Vaayu Foods",
    since: "2025-03-14T00:00:00Z",
    status: "active",
    tags: ["Wholesale", "VIP"],
    owner: { id: "m-1", name: "Asha Menon" },
    phone: "•••••• 4210",
    email: "p•••••@vaayu.example.com",
    city: "Bengaluru",
  },
  crm: { stage: "Negotiation", open_opportunities: 2, hot: true },
  orders: {
    count_90d: 5,
    total_90d: { amount_minor: 1_250_000, currency: "INR" },
    latest: { number: "SO-1042", status: "shipped", total: { amount_minor: 250_000, currency: "INR" } },
  },
  finance: {
    outstanding: { amount_minor: 48_000, currency: "USD" },
    overdue_count: 1,
    latest_invoice: { number: "INV-2231", status: "overdue", due_on: "2026-09-10" },
  },
  support: { open_tickets: 1, total_tickets: 4 },
  channels: null,
  timeline: [],
  unavailable: [],
};

const meta = {
  title: "Data display/Customer360View",
  component: Customer360View,
  args: {
    data: FULL,
    sectionLabel: (name: string) => LABELS[name] ?? name,
    sectionHref: (name: string) => (name === "orders" ? null : `https://example.test/${name}`),
  },
  render: (args) => (
    <div className="max-w-sm">
      <Customer360View {...args} />
    </div>
  ),
} satisfies Meta<typeof Customer360View>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Full: Story = {
  args: { revealSlot: <Button size="sm" variant="ghost">Reveal phone number</Button> },
};

/** Two sections the service could not read, and one it added after this bundle shipped. */
export const Partial: Story = {
  args: { data: { ...FULL, orders: null, finance: null, unavailable: ["orders", "finance", "delivery"] } },
};

export const PhoneOnly: Story = {
  args: { data: { ...FULL, customer: { ...FULL.customer, email: null } } },
};

export const LongNames: Story = {
  args: {
    data: {
      ...FULL,
      customer: {
        ...FULL.customer,
        name: "Venkatanarasimharajuvaripeta Subrahmanya Lakshminarayana",
        company: "Sri Lakshmi Venkateswara Agro Processing and Cold Storage Private Limited",
        owner: { id: "m-2", name: "Chandrasekharan Ramakrishnan Iyer" },
      },
    },
  },
};
