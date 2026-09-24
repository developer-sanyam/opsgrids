import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { RowActions } from "./RowActions";
import type { RowAction } from "./RowActions";

const ARCHIVE: RowAction = {
  label: "Archive",
  onSelect: fn(),
  disabled: true,
  disabledReason: "Paid invoices can't be archived.",
};

const ACTIONS: RowAction[] = [
  { label: "View", onSelect: fn() },
  { label: "Edit", onSelect: fn() },
  { label: "Duplicate", onSelect: fn() },
  ARCHIVE,
  { label: "Delete", onSelect: fn(), tone: "danger" },
];

const meta = {
  title: "Actions/RowActions",
  component: RowActions,
  args: { label: "INV-2026-000482", actions: ACTIONS.slice(0, 2) },
  render: (args) => (
    <div className="flex justify-end">
      <RowActions {...args} />
    </div>
  ),
} satisfies Meta<typeof RowActions>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Inline: Story = {};

export const Destructive: Story = {
  args: { actions: [{ label: "Edit", onSelect: fn() }, { label: "Delete", onSelect: fn(), tone: "danger" }] },
};

/** Present but not available now — focusable, with its reason read aloud. */
export const DisabledWithReason: Story = {
  args: { actions: [{ label: "Edit", onSelect: fn() }, ARCHIVE] },
};

/** More than `inlineLimit`: the rest collapse behind one "More actions" button. */
export const Overflow: Story = { args: { actions: ACTIONS } };

export const MenuOpen: Story = {
  tags: ["viewport"],
  args: { actions: ACTIONS },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole("button", { name: "More actions for INV-2026-000482" }));
    await canvas.findByRole("menu");
  },
};
