import { useState } from "react";
import type { ComponentProps } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { Switch } from "./Switch";

function Stateful(props: ComponentProps<typeof Switch>) {
  const [checked, setChecked] = useState(props.checked);
  return (
    <Switch
      {...props}
      checked={checked}
      description={
        checked
          ? "Requests are escalated to the next approver after 48 hours."
          : "Requests wait for the assigned approver, however long it takes."
      }
      onChange={(next) => {
        setChecked(next);
        props.onChange(next);
      }}
    />
  );
}

const meta = {
  title: "Forms/Switch",
  component: Switch,
  args: { label: "Escalate overdue requests", checked: true, onChange: fn() },
  render: (args) => (
    <div className="max-w-md">
      <Stateful {...args} />
    </div>
  ),
} satisfies Meta<typeof Switch>;

export default meta;
type Story = StoryObj<typeof meta>;

export const On: Story = {};

export const Off: Story = { args: { checked: false } };

export const Disabled: Story = { args: { disabled: true } };

export const WithoutDescription: Story = {
  render: (args) => (
    <div className="max-w-md">
      <Switch {...args} />
    </div>
  ),
};
