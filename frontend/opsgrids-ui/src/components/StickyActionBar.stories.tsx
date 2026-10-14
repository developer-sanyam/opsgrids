import type { Meta, StoryObj } from "@storybook/react-vite";

import { Button } from "./Button";
import { StickyActionBar } from "./StickyActionBar";

const meta = {
  title: "Layout/StickyActionBar",
  component: StickyActionBar,
  args: { always: true, children: null },
  decorators: [
    (Story) => (
      <div className="px-3">
        <p className="py-4 text-sm text-muted">The page scrolls behind the bar.</p>
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof StickyActionBar>;

export default meta;
type Story = StoryObj<typeof meta>;

export const ActionOnly: Story = {
  args: { children: <Button className="w-full">Receive goods</Button> },
};

export const WithSummary: Story = {
  args: {
    summary: (
      <div className="flex flex-col leading-tight">
        <span className="text-xs text-muted">Total (est.)</span>
        <span className="money text-base font-extrabold text-ink">$1,240.00</span>
      </div>
    ),
    children: <Button>Review invoice</Button>,
  },
};
