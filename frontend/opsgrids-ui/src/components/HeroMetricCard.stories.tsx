import type { Meta, StoryObj } from "@storybook/react-vite";

import { TrendingUpIcon } from "../icons";
import { HeroMetricCard } from "./HeroMetricCard";

const MONTHS = [9_00_000, 9_60_000, 10_40_000, 11_10_000, 11_80_000, 12_48_320];

const meta = {
  title: "Data display/HeroMetricCard",
  component: HeroMetricCard,
  args: {
    label: "Total Revenue",
    value: "₹12,48,320",
    icon: <TrendingUpIcon className="size-5" />,
    deltaPct: 12,
    caption: "from last month",
    bars: MONTHS,
  },
  render: (args) => (
    <div className="max-w-sm">
      <HeroMetricCard {...args} />
    </div>
  ),
} satisfies Meta<typeof HeroMetricCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithHistory: Story = {};

/** A first-ever reading: no delta to compare against, and the honest "This month" in its place. */
export const NoHistory: Story = {
  args: { deltaPct: undefined, caption: "This month", bars: undefined },
};

/** Fewer than a full trailing window still draws — three months, not six. */
export const ShortHistory: Story = {
  args: { bars: [10_40_000, 11_10_000, 11_80_000] },
};

/** The figure is representative until finance is wired: the amber chip says so, on the real card. */
export const SampleTrend: Story = {
  args: { sample: { label: "Sample trend", title: "Forecasting isn't connected to this workspace yet" } },
};
