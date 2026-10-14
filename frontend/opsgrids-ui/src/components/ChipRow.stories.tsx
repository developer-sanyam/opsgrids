import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";

import { ChipRow } from "./ChipRow";
import { Pill } from "./Pill";

const STATUSES = ["All", "New", "Working", "Qualified", "Disqualified", "Converted"];

function Chips() {
  const [active, setActive] = useState("All");
  return (
    <ChipRow label="Filter leads by status">
      {STATUSES.map((status) => (
        <Pill key={status} active={active === status} onClick={() => setActive(status)}>
          {status}
        </Pill>
      ))}
    </ChipRow>
  );
}

const meta = {
  title: "Navigation/ChipRow",
  component: ChipRow,
  subcomponents: { Pill },
  args: { label: "Filter leads by status", children: null },
} satisfies Meta<typeof ChipRow>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Wider than the frame: the right edge fades, and the fade moves as the row scrolls. */
export const Overflowing: Story = {
  render: () => (
    <div className="w-72">
      <Chips />
    </div>
  ),
};

/** Everything fits, so there is no fade. */
export const Fits: Story = {
  render: () => (
    <div className="w-full max-w-3xl">
      <Chips />
    </div>
  ),
};
