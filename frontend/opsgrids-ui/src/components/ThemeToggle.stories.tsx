import type { Meta, StoryObj } from "@storybook/react-vite";

import { ThemeProvider } from "./ThemeProvider";
import { ThemeToggle } from "./ThemeToggle";

/**
 * Every story already renders inside the real `ThemeProvider` (see `.storybook/preview.tsx`);
 * the toolbar's Theme control is that provider. The toggle here flips it from inside the story.
 */
const meta = {
  title: "Actions/ThemeToggle",
  component: ThemeToggle,
  subcomponents: { ThemeProvider },
  args: {},
} satisfies Meta<typeof ThemeToggle>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const InABar: Story = {
  render: () => (
    <div className="flex h-14 max-w-sm items-center gap-2 rounded-card bg-surface px-3 shadow-card">
      <span className="text-base font-extrabold tracking-tight">OpsGrids Revenue</span>
      <div className="flex-1" />
      <ThemeToggle />
    </div>
  ),
};
