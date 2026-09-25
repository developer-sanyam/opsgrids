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
