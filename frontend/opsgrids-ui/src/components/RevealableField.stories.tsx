import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { RevealableField } from "./RevealableField";
import type { RevealedFieldView } from "./RevealableField";

const MASKED: RevealedFieldView = { value: null, notice: null, pending: false, justHidden: false, reveal: fn(), hide: fn() };

const meta = {
  title: "Forms/RevealableField",
  component: RevealableField,
  args: { kind: "phone", masked: "•••••• 4821", canReveal: true, field: MASKED, stepUpIdle: true },
  render: (args) => (
    <div className="max-w-sm">
      <RevealableField {...args} />
    </div>
  ),
} satisfies Meta<typeof RevealableField>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Masked: Story = {};

export const Revealed: Story = { args: { field: { ...MASKED, value: "+91 98765 04821" } } };

export const RevealedEmail: Story = {
  args: { kind: "email", masked: "p•••••@vaayu.example.com", field: { ...MASKED, value: "priya@vaayu.example.com" } },
};

/** An HRMS identifier: plain text, named by its own label. */
export const RevealedText: Story = {
  args: { kind: "text", label: "PAN", noun: "PAN", masked: "••••••234Q", field: { ...MASKED, value: "AAPPK1234Q" } },
};

export const Refused: Story = {
  args: {
    field: { ...MASKED, notice: { tone: "error", text: "This lead is not assigned to you, so you cannot reveal its details." } },
  },
};

export const NothingOnFile: Story = { args: { masked: null } };

export const WithoutPermission: Story = { args: { canReveal: false } };
