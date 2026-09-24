import type { Meta, StoryObj } from "@storybook/react-vite";

import { SampleChip } from "./SampleChip";

const meta = {
  title: "Feedback/SampleChip",
  component: SampleChip,
  args: {},
} satisfies Meta<typeof SampleChip>;

export default meta;
type Story = StoryObj<typeof meta>;

export const SampleData: Story = {};

/** The headline is live; only its delta and sparkline are stand-ins. */
export const SampleTrend: Story = {
  args: { label: "Sample trend", title: "Forecasting isn't connected to this workspace yet" },
};
