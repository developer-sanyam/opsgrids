import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";

import { BarChartIcon } from "../icons";
import { Button } from "./Button";
import { ChartCard } from "./ChartCard";
import { DonutChart } from "./DonutChart";
import { LineChart } from "./LineChart";
import { Select } from "./Select";
import { EmptyState, ErrorState } from "./States";
import { Tabs } from "./Tabs";

const WEEKS = ["25 Aug", "1 Sep", "8 Sep", "15 Sep", "22 Sep"];

const GROWTH = (
  <LineChart
    label="New accounts per week"
    categories={WEEKS}
    series={[{ key: "accounts", label: "New accounts", values: [22, 48, 66, 91, 137] }]}
  />
);

const PERIOD = (
  <Select
    label="Period"
    hideLabel
    defaultValue="new"
    options={[
      { value: "new", label: "New accounts" },
      { value: "churned", label: "Churned accounts" },
    ]}
  />
);

function RangeTabs() {
  const [range, setRange] = useState("30d");
  return (
    <Tabs
      label="Range"
      variant="segmented"
      value={range}
      onChange={setRange}
      items={[
        { id: "7d", label: "7D" },
        { id: "30d", label: "30D" },
        { id: "90d", label: "90D" },
      ]}
    />
  );
}

const meta = {
  title: "Charts/ChartCard",
  component: ChartCard,
  args: { title: "Account growth", description: "New accounts per week", children: GROWTH },
  render: (args) => (
    <div className="max-w-3xl">
      <ChartCard {...args} />
    </div>
  ),
} satisfies Meta<typeof ChartCard>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A chart with the control that changes what it shows. */
export const WithPeriodSelect: Story = { args: { action: PERIOD } };

export const WithRangeTabs: Story = { args: { action: <RangeTabs />, description: undefined } };

export const Donut: Story = {
  args: {
    title: "Accounts by status",
    description: undefined,
    action: (
      <Button variant="ghost" size="sm">
        View all
      </Button>
    ),
    children: (
      <DonutChart
        label="Accounts by status"
        slices={[
          { key: "active", label: "Active", value: 112, tone: "positive" },
          { key: "trial", label: "Trial", value: 12, tone: "warning" },
          { key: "suspended", label: "Suspended", value: 4, tone: "danger" },
        ]}
      />
    ),
  },
};

/** Loading in the chart's own shape, so the grid does not jump when it lands. */
export const Loading: Story = { args: { loading: true } };

export const LoadingDonut: Story = { args: { loading: true, skeleton: "donut", title: "Revenue by plan" } };

export const Failed: Story = {
  args: {
    error: (
      <ErrorState
        title="Growth didn't load"
        message="The console service didn't answer. Nothing was changed."
        action={
          <Button size="sm" variant="outline">
            Try again
          </Button>
        }
      />
    ),
  },
};

export const Empty: Story = {
  args: {
    empty: (
      <EmptyState
        icon={<BarChartIcon className="size-6" />}
        title="No accounts yet"
        description="Growth appears here once the first account signs up."
      />
    ),
  },
};

/** Representative data, marked as such — never shown as if it were live. */
export const Sample: Story = { args: { sample: true } };
