import type { Meta, StoryObj } from "@storybook/react-vite";

import { Card } from "./Card";
import { FieldGrid } from "./FieldGrid";
import { TextField } from "./TextField";

const meta = {
  title: "Forms/FieldGrid",
  component: FieldGrid,
  args: { children: null },
} satisfies Meta<typeof FieldGrid>;

export default meta;
type Story = StoryObj<typeof meta>;

/**
 * "Estimated unit price (optional)" wraps in a third of this card. The three inputs still share
 * one top edge, and the helper text under the first and second does not move the third.
 */
export const WrappedLabelAndHelpers: Story = {
  render: () => (
    <Card className="max-w-2xl p-4">
      <FieldGrid className="sm:grid-cols-3">
        <TextField label="Quantity" required helper="Choose an item to see its unit." />
        <TextField label="Estimated unit price" optional helper="Major units, like 24000.50." />
        <TextField label="Note" optional />
      </FieldGrid>
    </Card>
  ),
};

export const WithAnError: Story = {
  render: () => (
    <Card className="max-w-2xl p-4">
      <FieldGrid className="sm:grid-cols-2">
        <TextField label="Amount" helper="How long each approver gets." error="Enter a number of zero or more." />
        <TextField label="Unit" />
      </FieldGrid>
    </Card>
  ),
};
