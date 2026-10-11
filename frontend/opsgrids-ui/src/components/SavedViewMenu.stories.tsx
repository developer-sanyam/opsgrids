import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { SavedViewMenu } from "./SavedViewMenu";
import type { SavedViewItem } from "./SavedViewMenu";

const VIEWS: SavedViewItem[] = [
  { id: "sys-mine", name: "My open deals", visibility: "private", mine: true, system: true },
  { id: "sys-closing", name: "Closing this month", visibility: "private", mine: true, system: true },
  { id: "v1", name: "Hot in Pune", visibility: "private", mine: true },
  { id: "v2", name: "Q4 renewals", visibility: "org", mine: false },
];

const meta = {
  title: "Data display/SavedViewMenu",
  component: SavedViewMenu,
  args: {
    views: VIEWS,
    activeId: null,
    dirty: false,
    canShare: true,
    onSelect: fn(),
    onSave: fn(),
    onUpdate: fn(),
    onDelete: fn(),
  },
} satisfies Meta<typeof SavedViewMenu>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = { args: { views: [] } };

export const Populated: Story = {
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole("button", { name: "Views" }));
  },
};

/** The person's own view, changed since it was saved: a dot that says so, and Update offered. */
export const Dirty: Story = {
  args: { activeId: "v1", dirty: true },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole("button", { name: /^Views/ }));
  },
};

/** A view someone shared: selectable, never updated or deleted from here. */
export const Shared: Story = { args: { activeId: "v2" } };

export const PhoneWidth: Story = {
  args: { activeId: "v1", dirty: true },
  render: (args) => (
    <div className="max-w-xs">
      <SavedViewMenu {...args} />
    </div>
  ),
};
