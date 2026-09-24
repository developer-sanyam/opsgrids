import type { Meta, StoryObj } from "@storybook/react-vite";

import { DeltaPill } from "./DeltaPill";

const meta = {
  title: "Data display/DeltaPill",
  component: DeltaPill,
  args: { value: 12.4 },
} satisfies Meta<typeof DeltaPill>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rising: Story = {};

export const Falling: Story = { args: { value: -3.75 } };

/**
 * Colour follows whether the direction is good; the arrow and sign follow the number. A falling
 * cost is green with a down arrow, a rising one red with an up arrow.
 */
export const GoodWhenDown: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <DeltaPill value={-8.2} goodWhen="down" />
      <DeltaPill value={5.1} goodWhen="down" />
      <DeltaPill value={0} />
      <DeltaPill value={148.93} />
    </div>
  ),
};
