import type { Meta, StoryObj } from "@storybook/react-vite";

import { TextField } from "./TextField";

const meta = {
  title: "Forms/TextField",
  component: TextField,
  args: { label: "Company name", defaultValue: "Acme Industries" },
  render: (args) => (
    <div className="max-w-sm">
      <TextField {...args} />
    </div>
  ),
} satisfies Meta<typeof TextField>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Filled: Story = {};

export const Empty: Story = { args: { defaultValue: "", placeholder: "e.g. Acme Industries" } };

export const WithHelper: Story = {
  args: { label: "Domain", defaultValue: "acme.example", helper: "Used to match incoming email to this company." },
};

export const Optional: Story = { args: { label: "Website", defaultValue: "", optional: true, type: "url" } };

export const WithError: Story = {
  args: {
    label: "Work email",
    type: "email",
    defaultValue: "priya@acme",
    helper: "We send the invitation here.",
    error: "Enter a complete email address, like name@company.com.",
  },
};

export const Disabled: Story = { args: { disabled: true } };

export const LongValue: Story = {
  args: { defaultValue: "Consolidated Northern & Southern Agricultural Cooperative Federation Private Limited" },
};
