import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { Button } from "./Button";
import { BulkActionBar } from "./BulkActionBar";

const meta = {
  title: "Data display/BulkActionBar",
  component: BulkActionBar,
  args: {
    count: 3,
    onClear: fn(),
    children: (
      <>
        <Button variant="outline" size="sm">
          Change plan
        </Button>
        <Button variant="outline" size="sm">
          Export
        </Button>
      </>
    ),
  },
} satisfies Meta<typeof BulkActionBar>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

/** The destructive action is `danger` — and still goes through a ConfirmDialog naming the count. */
export const WithDestructive: Story = {
  args: {
    count: 12,
    children: (
      <>
        <Button variant="outline" size="sm">
          Activate
        </Button>
        <Button variant="danger" size="sm">
          Suspend 12 accounts
        </Button>
      </>
    ),
  },
};

/** More actions than a phone's width: they wrap under the count, never off the edge. */
export const ManyActions: Story = {
  args: {
    count: 1,
    children: ["Change plan", "Assign owner", "Add tag", "Export", "Send reminder"].map((label) => (
      <Button key={label} variant="outline" size="sm">
        {label}
      </Button>
    )),
  },
};
