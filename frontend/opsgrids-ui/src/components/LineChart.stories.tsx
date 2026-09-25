import type { Meta, StoryObj } from "@storybook/react-vite";

import { Card } from "./Card";
import { LineChart } from "./LineChart";

const WEEKS = ["25 Aug", "1 Sep", "8 Sep", "15 Sep", "22 Sep", "29 Sep", "6 Oct", "13 Oct", "20 Oct"];

const lakh = (value: number) => `₹${(value / 100000).toLocaleString("en-IN", { maximumFractionDigits: 1 })}L`;

const meta = {
  title: "Charts/LineChart",
  component: LineChart,
  args: {
    label: "New accounts per week",
    categories: WEEKS,
    series: [{ key: "accounts", label: "New accounts", values: [22, 48, 41, 66, 58, 91, 84, 118, 137] }],
  },
  render: (args) => (
    <Card className="max-w-3xl p-4 sm:p-6">
      <LineChart {...args} />
    </Card>
  ),
} satisfies Meta<typeof LineChart>;

export default meta;
type Story = StoryObj<typeof meta>;

/** One quantity over time: an area, from zero to a round number. */
export const Area: Story = {};

/** Two series with meaning in their colours: inflow in the accent, outflow in danger, and a legend. */
export const TwoSeries: Story = {
  args: {
    label: "Cash in and out by month",
    categories: ["Apr", "May", "Jun", "Jul", "Aug", "Sep"],
    formatValue: lakh,
    series: [
      { key: "in", label: "Inflow", values: [1840000, 2100000, 1950000, 2480000, 2620000, 2910000] },
      { key: "out", label: "Outflow", values: [1520000, 1680000, 1790000, 1840000, 2210000, 2050000], tone: "danger" },
    ],
  },
};

/** The keyboard's reading: focus shows the latest point, Left and Right step through the rest. */
export const KeyboardFocus: Story = {
  play: async ({ userEvent }) => {
    await userEvent.tab();
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
  },
};

/** Thirty days: labels thin to every fifth so they never collide on a phone. */
export const ManyPoints: Story = {
  args: {
    label: "Tickets opened per day",
    categories: Array.from({ length: 30 }, (_, index) => `${index + 1} Sep`),
    series: [
      {
        key: "tickets",
        label: "Tickets",
        values: Array.from({ length: 30 }, (_, index) => 20 + ((index * 7) % 13) + Math.round(index / 3)),
      },
    ],
  },
};

/** A net figure that dips below zero: the axis extends down to a round number, zero still on it. */
export const NegativeValues: Story = {
  args: {
    label: "Net cash flow by month",
    categories: ["Apr", "May", "Jun", "Jul", "Aug", "Sep"],
    formatValue: lakh,
    area: false,
    series: [{ key: "net", label: "Net", values: [320000, -180000, 160000, -60000, 410000, 860000] }],
  },
};

/** A caller that knows its card is phone-width thins the axis itself with `categoryStride`,
 * rather than the default's fixed room for six — twelve month names need every third, not every
 * second, to clear a narrow card. */
export const CustomStride: Story = {
  args: {
    label: "Revenue by month",
    categories: ["Oct '25", "Nov", "Dec", "Jan '26", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"],
    categoryStride: 3,
    formatValue: lakh,
    series: [
      {
        key: "revenue",
        label: "Revenue",
        values: [860000, 875000, 910000, 895000, 930000, 960000, 985000, 1005000, 1040000, 1075000, 1114500, 1248320],
      },
    ],
  },
  render: (args) => (
    <Card className="max-w-xs p-4 sm:p-6">
      <LineChart {...args} />
    </Card>
  ),
};

/** Nothing yet: a flat line at zero on a small default axis, not a divide-by-zero. */
export const AllZero: Story = {
  args: {
    label: "Refunds per week",
    series: [{ key: "refunds", label: "Refunds", values: WEEKS.map(() => 0) }],
  },
};
