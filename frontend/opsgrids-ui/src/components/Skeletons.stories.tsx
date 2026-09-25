import type { Meta, StoryObj } from "@storybook/react-vite";

import { Card } from "./Card";
import { CardSkeleton, ChartSkeleton, DashboardSkeleton, FormSkeleton, TableSkeleton } from "./Skeletons";

/**
 * The composed loading states (brief §12–14). Each is one status region that says what is
 * loading; the harness photographs the pulse at its first frame.
 */
const meta = {
  title: "Feedback/Skeletons",
  component: DashboardSkeleton,
  subcomponents: { TableSkeleton, CardSkeleton, FormSkeleton, ChartSkeleton },
} satisfies Meta<typeof DashboardSkeleton>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Header, four KPIs (two-up on a phone), a large chart beside a donut, then a table. */
export const Dashboard: Story = {};

export const Table: Story = {
  render: () => (
    <Card className="p-4 md:p-5">
      <TableSkeleton label="Loading accounts" rows={6} columns={5} />
    </Card>
  ),
};

export const CardPreset: Story = {
  name: "Card",
  render: () => (
    <div className="grid max-w-3xl gap-4 md:grid-cols-2">
      <CardSkeleton label="Loading recent activity" />
      <CardSkeleton label="Loading pending approvals" lines={5} />
    </div>
  ),
};

export const Form: Story = {
  render: () => (
    <Card className="max-w-3xl p-4 md:p-6">
      <FormSkeleton label="Loading the purchase order" fields={6} columns={2} />
    </Card>
  ),
};

export const Charts: Story = {
  render: () => (
    <div className="grid gap-4 xl:grid-cols-3">
      <Card className="p-5">
        <ChartSkeleton label="Loading account growth" variant="area" />
      </Card>
      <Card className="p-5">
        <ChartSkeleton label="Loading tickets per day" variant="bar" />
      </Card>
      <Card className="p-5">
        <ChartSkeleton label="Loading revenue by plan" variant="donut" />
      </Card>
    </div>
  ),
};
