import type { Meta, StoryObj } from "@storybook/react-vite";

import { BarChart } from "./BarChart";
import { Card } from "./Card";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const meta = {
  title: "Charts/BarChart",
  component: BarChart,
  args: {
    label: "Tickets opened and solved, last 7 days",
    categories: DAYS,
    series: [
      { key: "opened", label: "Opened", values: [42, 38, 51, 47, 35, 12, 9] },
      { key: "solved", label: "Solved", values: [39, 41, 44, 50, 38, 15, 7], tone: "positive" },
    ],
  },
  render: (args) => (
    <Card className="max-w-3xl p-4 sm:p-6">
      <BarChart {...args} />
    </Card>
  ),
} satisfies Meta<typeof BarChart>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Two series side by side per day, with a legend. */
export const Grouped: Story = {};

export const SingleSeries: Story = {
  args: {
    label: "Purchase orders raised per month",
    categories: ["Apr", "May", "Jun", "Jul", "Aug", "Sep"],
    series: [{ key: "orders", label: "Orders", values: [18, 24, 21, 30, 27, 34] }],
  },
};

/** The keyboard's reading of one day. */
export const KeyboardFocus: Story = {
  play: async ({ userEvent }) => {
    await userEvent.tab();
    await userEvent.keyboard("{Home}{ArrowRight}{ArrowRight}");
  },
};

/** A month of days: bars narrow, labels thin out. */
export const ManyCategories: Story = {
  args: {
    label: "Orders per day, September",
    categories: Array.from({ length: 30 }, (_, index) => `${index + 1}`),
    series: [{ key: "orders", label: "Orders", values: Array.from({ length: 30 }, (_, index) => 4 + ((index * 5) % 11)) }],
  },
};
