import { useState } from "react";
import type { ComponentProps } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { RadioGroup } from "./RadioGroup";
import type { RadioOption } from "./RadioGroup";
import { Tag } from "./Tag";

const OPTIONS: RadioOption[] = [
  { value: "present", label: "Present", description: "Counts as a full working day." },
  { value: "half_day", label: "Half day", description: "Deducts half a day from earned leave." },
  {
    value: "absent",
    label: "Absent",
    description: "Unpaid. Payroll for September has locked, so this applies to October.",
    aside: <Tag tone="warning">Affects pay</Tag>,
  },
  { value: "holiday", label: "Holiday", description: "Only an HR admin can mark a holiday.", disabled: true },
];

function Stateful(props: ComponentProps<typeof RadioGroup>) {
  const [value, setValue] = useState(props.value);
  return (
    <RadioGroup
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
  title: "Forms/RadioGroup",
  component: RadioGroup,
  args: {
    legend: "Change to",
    name: "attendance-correction",
    value: "half_day",
    onChange: fn(),
    options: OPTIONS,
  },
  render: (args) => (
    <div className="max-w-lg">
      <Stateful {...args} />
    </div>
  ),
} satisfies Meta<typeof RadioGroup>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const NothingChosen: Story = { args: { value: null, helper: "The original entry stays until this is approved." } };

export const WithError: Story = { args: { value: null, error: "Choose what the day should be changed to." } };
