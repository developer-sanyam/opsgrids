import { useState } from "react";
import type { ComponentProps } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { DateRangeField } from "./DateRangeField";

function Stateful(props: ComponentProps<typeof DateRangeField>) {
  const [start, setStart] = useState(props.start);
  const [end, setEnd] = useState(props.end);
  return (
    <DateRangeField
      {...props}
      start={start}
      end={end}
      onStartChange={(next) => {
        setStart(next);
        props.onStartChange(next);
      }}
      onEndChange={(next) => {
        setEnd(next);
        props.onEndChange(next);
      }}
    />
  );
}

const meta = {
  title: "Forms/DateRangeField",
  component: DateRangeField,
  args: {
    legend: "When the delegation runs",
    start: "2026-09-20T03:30:00.000Z",
    end: "2026-09-27T12:30:00.000Z",
    onStartChange: fn(),
    onEndChange: fn(),
    zone: "Asia/Kolkata",
    zoneNote: "Times are in Bengaluru time (IST).",
  },
  render: (args) => (
    <div className="max-w-2xl">
      <Stateful {...args} />
    </div>
  ),
} satisfies Meta<typeof DateRangeField>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Filled: Story = {};

export const Empty: Story = { args: { start: null, end: null } };

export const WithErrors: Story = {
  args: {
    endError: "The end must be after the start.",
    end: "2026-09-19T03:30:00.000Z",
  },
};

export const Disabled: Story = { args: { disabled: true } };
