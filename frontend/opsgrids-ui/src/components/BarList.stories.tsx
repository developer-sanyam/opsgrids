import type { Meta, StoryObj } from "@storybook/react-vite";

import { BarList } from "./BarList";
import { Card } from "./Card";

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

const meta = {
  title: "Charts/BarList",
  component: BarList,
  args: {
    label: "Committed spend by category",
    formatValue: (value: number) => inr.format(value),
    items: [
      { key: "raw", label: "Raw material", value: 1240000 },
      { key: "packaging", label: "Packaging", value: 486000 },
      { key: "freight", label: "Freight", value: 212000 },
      { key: "services", label: "Services", value: 98000 },
      { key: "other", label: "Other", value: 41000 },
    ],
  },
  render: (args) => (
    <Card className="max-w-md p-4 md:p-5">
      <BarList {...args} />
    </Card>
  ),
} satisfies Meta<typeof BarList>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

/** Counts where the share is noise: values only. */
export const WithoutShare: Story = {
  args: {
    label: "Open deals by stage",
    formatValue: (value: number) => `${value} deals`,
    showShare: false,
    items: [
      { key: "qualified", label: "Qualified", value: 34 },
      { key: "proposal", label: "Proposal", value: 21 },
      { key: "negotiation", label: "Negotiation", value: 9 },
      { key: "won", label: "Won", value: 4 },
    ],
  },
};

/** One row's colour for everything — or a semantic one, like overdue ageing in `danger`. */
export const Toned: Story = {
  args: {
    label: "Receivables overdue by age",
    tone: "danger",
    items: [
      { key: "30", label: "1–30 days", value: 820000 },
      { key: "60", label: "31–60 days", value: 410000 },
      { key: "90", label: "61–90 days", value: 150000 },
      { key: "90+", label: "Over 90 days", value: 62000 },
    ],
  },
};

export const LongLabels: Story = {
  args: {
    label: "Spend by supplier",
    items: [
      { key: "a", label: "Consolidated Northern & Southern Agricultural Cooperative Federation", value: 1240000 },
      { key: "b", label: "Kaveri Textiles and Handloom Exporters Private Limited", value: 486000 },
      { key: "c", label: "Bluefin Logistics", value: 0 },
    ],
  },
};
