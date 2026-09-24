import type { Meta, StoryObj } from "@storybook/react-vite";

import { Card } from "./Card";
import { SectionHeader } from "./States";

const meta = {
  title: "Layout/Card",
  component: Card,
  args: { tone: "surface" },
  render: (args) => (
    <Card {...args} className="flex max-w-md flex-col gap-2 p-5">
      <SectionHeader title="Pipeline this quarter" subtitle="Open deals by stage" />
      <p className="text-sm">
        Cards hold one idea each. The surface card is the default; the dark card is the single
        focal panel on a screen.
      </p>
    </Card>
  ),
} satisfies Meta<typeof Card>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Surface: Story = {};

export const Dark: Story = { args: { tone: "dark" } };

export const Empty: Story = {
  render: (args) => <Card {...args} className="h-32 max-w-md" />,
};
