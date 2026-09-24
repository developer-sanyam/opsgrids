import { useState } from "react";
import type { ComponentProps } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { DateTimeField } from "./DateTimeField";

function Stateful(props: ComponentProps<typeof DateTimeField>) {
  const [value, setValue] = useState(props.value);
  return (
    <DateTimeField
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
  title: "Forms/DateTimeField",
  component: DateTimeField,
  args: {
    label: "Starts",
    value: "2026-09-20T03:30:00.000Z",
    onChange: fn(),
    zone: "Asia/Kolkata",
    helper: "Bengaluru time (IST).",
  },
  render: (args) => (
    <div className="max-w-sm">
      <Stateful {...args} />
    </div>
  ),
} satisfies Meta<typeof DateTimeField>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Filled: Story = {};

export const Empty: Story = { args: { value: null } };

/** The same instant, shown on a New York wall clock. */
export const OtherZone: Story = { args: { zone: "America/New_York", helper: "New York time (EDT)." } };

export const WithError: Story = { args: { error: "The start must be in the future." } };

export const Disabled: Story = { args: { disabled: true } };
