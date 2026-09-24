import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { Select } from "./Select";

const meta = {
  title: "Forms/Select",
  component: Select,
  args: {
    label: "Warehouse",
    options: [
      { value: "blr", label: "Bengaluru — Peenya" },
      { value: "pun", label: "Pune — Chakan" },
      { value: "del", label: "Delhi NCR — Manesar" },
      { value: "chn", label: "Chennai — Sriperumbudur (closed for audit)", disabled: true },
    ],
    defaultValue: "blr",
    onChange: fn(),
  },
  render: (args) => (
    <div className="max-w-sm">
      <Select {...args} />
    </div>
  ),
} satisfies Meta<typeof Select>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Chosen: Story = {};

export const Placeholder: Story = {
  args: { defaultValue: "", placeholder: "Choose a warehouse", helper: "Stock is received into this warehouse." },
};

export const WithError: Story = {
  args: { defaultValue: "", placeholder: "Choose a warehouse", error: "Choose where this stock is received." },
};

export const Disabled: Story = { args: { disabled: true } };
