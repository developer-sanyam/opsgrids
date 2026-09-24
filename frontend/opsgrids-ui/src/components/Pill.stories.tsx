import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";

import { ClockIcon, FilterIcon } from "../icons";
import { Pill } from "./Pill";

const FILTERS = ["All", "Mine", "Unassigned", "Overdue"];

function FilterRow() {
  const [active, setActive] = useState("All");
  return (
    <div className="flex flex-wrap gap-1">
      {FILTERS.map((filter) => (
        <Pill key={filter} active={filter === active} onClick={() => setActive(filter)}>
          {filter}
        </Pill>
      ))}
    </div>
  );
}

const meta = {
  title: "Actions/Pill",
  component: Pill,
  args: { children: "Mine", active: false },
} satisfies Meta<typeof Pill>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Inactive: Story = {};

export const Active: Story = { args: { active: true } };

export const WithIcon: Story = {
  render: () => (
    <div className="flex gap-1">
      <Pill leadingIcon={<FilterIcon className="size-4" />}>Filters</Pill>
      <Pill active leadingIcon={<ClockIcon className="size-4" />}>
        Due this week
      </Pill>
    </div>
  ),
};

export const Disabled: Story = { args: { disabled: true } };

export const FilterGroup: Story = { render: () => <FilterRow /> };
