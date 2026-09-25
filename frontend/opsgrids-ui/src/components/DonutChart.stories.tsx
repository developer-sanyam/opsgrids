import type { Meta, StoryObj } from "@storybook/react-vite";

import { Card } from "./Card";
import { DonutChart } from "./DonutChart";

const meta = {
  title: "Charts/DonutChart",
  component: DonutChart,
  args: {
    label: "Accounts by status",
    slices: [
      { key: "active", label: "Active", value: 112, tone: "positive" },
      { key: "trial", label: "Trial", value: 12, tone: "warning" },
      { key: "suspended", label: "Suspended", value: 4, tone: "danger" },
    ],
  },
  render: (args) => (
    <Card className="max-w-md p-4 sm:p-6">
      <DonutChart {...args} />
    </Card>
  ),
} satisfies Meta<typeof DonutChart>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Statuses, coloured by what they mean. */
export const ByStatus: Story = {};

/** Plans in the default series order, money in the middle. */
export const RevenueByPlan: Story = {
  args: {
    label: "Revenue by plan, this month",
    centerValue: "₹12.4L",
    centerLabel: "Total",
    formatValue: (value: number) => `₹${(value / 100000).toLocaleString("en-IN", { maximumFractionDigits: 1 })}L`,
    slices: [
      { key: "enterprise", label: "Enterprise", value: 520000 },
      { key: "pro", label: "Pro", value: 347000 },
      { key: "business", label: "Business", value: 223000 },
      { key: "starter", label: "Starter", value: 124000 },
      { key: "custom", label: "Custom", value: 25000 },
    ],
  },
};

/** Nothing counted yet: the empty ring and a zero total. */
export const Empty: Story = {
  args: {
    slices: [
      { key: "active", label: "Active", value: 0, tone: "positive" },
      { key: "trial", label: "Trial", value: 0, tone: "warning" },
    ],
  },
};

/**
 * The `sm` ring, for a donut sharing one row with two other cards (a Super Admin dashboard's
 * Account Growth, Revenue by Plan and Accounts by Status at 1280px) — a narrower card than the
 * default story's, where the full-size ring would leave a long label and a wide money value
 * fighting over what's left and truncating the label to a single letter.
 */
export const Compact: Story = {
  args: { size: "sm" },
  render: (args) => (
    <Card className="max-w-72 p-4">
      <DonutChart {...args} />
    </Card>
  ),
};

/**
 * `showValue={false}` and `layout="stacked"` together, for the narrowest column of all — a money
 * value is dropped from the visible legend (it is still in the row's `title` and the data table
 * below) and the legend never sits beside the ring, whatever the viewport, so "Operations 38%"
 * reads whole instead of "Ope… 38%".
 */
export const NameAndShareOnly: Story = {
  args: {
    label: "Revenue by plan, this month",
    size: "sm",
    showValue: false,
    layout: "stacked",
    centerValue: "₹97K",
    formatValue: (value: number) => `₹${(value / 100000).toLocaleString("en-IN", { maximumFractionDigits: 1 })}L`,
    slices: [
      { key: "operations", label: "Operations", value: 37099 },
      { key: "hrms", label: "HRMS", value: 27299 },
      { key: "itsm", label: "ITSM", value: 24800 },
      { key: "crm", label: "CRM", value: 4998 },
      { key: "finance", label: "Finance", value: 2499 },
    ],
  },
  render: (args) => (
    <Card className="max-w-72 p-4">
      <DonutChart {...args} />
    </Card>
  ),
};
