import { useState } from "react";
import type { ComponentProps } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { CheckboxGroup } from "./CheckboxGroup";
import type { CheckboxOption } from "./CheckboxGroup";

const ROLES: CheckboxOption[] = [
  { value: "owner", label: "Owner", description: "Everything, including billing and deleting the organization." },
  { value: "revenue_admin", label: "Revenue admin", description: "Manages the pipeline, owners and every deal." },
  { value: "sales_rep", label: "Sales rep", description: "Works their own deals and contacts." },
  { value: "viewer", label: "Viewer", description: "Reads reports. Changes nothing.", disabled: true },
];

const DAYS: CheckboxOption[] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => ({
  value: day.toLowerCase(),
  label: day,
}));

/** Holds the selection so the workshop is interactive; the component itself is controlled. */
function Stateful(props: ComponentProps<typeof CheckboxGroup>) {
  const [value, setValue] = useState(props.value);
  return (
    <CheckboxGroup
      {...props}
      value={value}
      onChange={(next) => {
        setValue(next);
        props.onChange(next);
      }}
    />
  );
}

const meta = {
  title: "Forms/CheckboxGroup",
  component: CheckboxGroup,
  args: {
    label: "Roles",
    options: ROLES,
    value: ["sales_rep"],
    onChange: fn(),
    helper: "A member can hold several roles; permissions are the union.",
  },
  render: (args) => (
    <div className="max-w-lg">
      <Stateful {...args} />
    </div>
  ),
} satisfies Meta<typeof CheckboxGroup>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const NoneSelected: Story = { args: { value: [] } };

export const WithError: Story = {
  args: { value: [], error: "Choose at least one role." },
};

export const Horizontal: Story = {
  args: {
    label: "Working days",
    options: DAYS,
    value: ["mon", "tue", "wed", "thu", "fri"],
    orientation: "horizontal",
    helper: undefined,
  },
};
