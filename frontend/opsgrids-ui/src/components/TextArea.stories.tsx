import { useState } from "react";
import type { ComponentProps } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";

import { TextArea } from "./TextArea";

function Stateful(props: ComponentProps<typeof TextArea>) {
  const [value, setValue] = useState(typeof props.value === "string" ? props.value : "");
  return <TextArea {...props} value={value} onChange={(event) => setValue(event.target.value)} />;
}

const meta = {
  title: "Forms/TextArea",
  component: TextArea,
  args: {
    label: "Comment",
    value: "Approved — the client confirmed the revised scope on the call this morning.",
    helper: "The requester sees this with your decision.",
  },
  render: (args) => (
    <div className="max-w-lg">
      <Stateful {...args} />
    </div>
  ),
} satisfies Meta<typeof TextArea>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Filled: Story = {};

export const EmptyOptional: Story = { args: { value: "", optional: true, placeholder: "Add context for the requester" } };

/** The counter appears near the limit, reading as a warning rather than a rule to obey. */
export const NearLimit: Story = {
  args: {
    maxLength: 120,
    counterFrom: 90,
    value: "Rejected: the dates overlap the quarter-end close, and two of the three people on the team are already away.",
  },
};

export const WithError: Story = { args: { value: "", error: "A reason is required when you reject a request." } };

export const Disabled: Story = { args: { disabled: true } };
