import type { Meta, StoryObj } from "@storybook/react-vite";

import { Sparkline } from "./Sparkline";

const meta = {
  title: "Data display/Sparkline",
  component: Sparkline,
  args: { data: [42, 45, 44, 48, 51, 50, 55, 58, 57, 62], tone: "good" },
} satisfies Meta<typeof Sparkline>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Good: Story = {};

export const Bad: Story = { args: { data: [62, 60, 61, 55, 52, 53, 48, 44, 45, 40], tone: "bad" } };

/** A flat series and a single point must still draw, not divide by zero. */
export const Degenerate: Story = {
  render: () => (
    <div className="flex items-center gap-6">
      <Sparkline data={[5, 5, 5, 5, 5]} tone="good" />
      <Sparkline data={[7]} tone="bad" />
    </div>
  ),
};

export const Wide: Story = { args: { width: 240, height: 64 } };
