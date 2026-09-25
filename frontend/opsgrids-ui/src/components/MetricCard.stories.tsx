import type { Meta, StoryObj } from "@storybook/react-vite";

import { BuildingIcon, CheckCircleIcon, ClockIcon, MinusCircleIcon, UsersIcon, WalletIcon } from "../icons";
import { MetricCard } from "./MetricCard";

const meta = {
  title: "Data display/MetricCard",
  component: MetricCard,
  args: {
    label: "Total accounts",
    value: "128",
    icon: <BuildingIcon className="size-5" />,
    trend: { value: 12, period: "vs last month" },
  },
  render: (args) => (
    <div className="max-w-xs">
      <MetricCard {...args} />
    </div>
  ),
} satisfies Meta<typeof MetricCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithTrend: Story = {};

/** No comparison exists: no trend is drawn — not a dash, not a zero. */
export const NoTrend: Story = { args: { trend: undefined, label: "Total users", value: "1,248", icon: <UsersIcon className="size-5" />, tone: "info" } };

/** Fewer suspended accounts is good: the fall's arrow is green, and it still points down. */
export const GoodWhenDown: Story = {
  args: {
    label: "Suspended accounts",
    value: "4",
    tone: "danger",
    icon: <MinusCircleIcon className="size-5" />,
    trend: { value: -33, goodWhen: "down", period: "vs last month" },
  },
};

export const Worsening: Story = {
  args: {
    label: "Overdue invoices",
    value: "₹3,12,400",
    tone: "warning",
    icon: <ClockIcon className="size-5" />,
    trend: { value: 18.5, goodWhen: "down", period: "vs last week" },
  },
};

/** The desktop KPI row: four across at 1280px, two on a tablet, one on a phone. */
export const Row: Story = {
  render: () => (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <MetricCard label="Total accounts" value="128" icon={<BuildingIcon className="size-5" />} trend={{ value: 12, period: "vs last month" }} />
      <MetricCard label="Active accounts" value="112" tone="positive" icon={<CheckCircleIcon className="size-5" />} trend={{ value: 8 }} />
      <MetricCard label="Trial accounts" value="12" tone="warning" icon={<ClockIcon className="size-5" />} trend={{ value: 20 }} />
      <MetricCard label="Monthly revenue" value="₹12,48,320" tone="cyan" icon={<WalletIcon className="size-5" />} trend={{ value: 14, period: "vs last month" }} />
    </div>
  ),
};

/** The phone home's 2×2 grid (mock-2, mock-5): icon still to the figures' left, at phone scale. */
export const CompactRow: Story = {
  render: () => (
    <div className="grid max-w-sm grid-cols-2 gap-3">
      <MetricCard size="compact" label="Invoices" value="168" icon={<BuildingIcon className="size-4" />} trend={{ value: 12 }} />
      <MetricCard size="compact" label="Customers" value="856" tone="positive" icon={<CheckCircleIcon className="size-4" />} trend={{ value: 8 }} />
      <MetricCard size="compact" label="Pending Tasks" value="18" tone="warning" icon={<ClockIcon className="size-4" />} trend={{ value: -6, goodWhen: "down" }} />
      <MetricCard
        size="compact"
        label="Overdue Invoices"
        value="6"
        tone="danger"
        icon={<MinusCircleIcon className="size-4" />}
        trend={{ value: -33, goodWhen: "down" }}
      />
    </div>
  ),
};

/** Four across in one row (mock-1's phone and tablet Accounts/Active/Trial/Suspended row): the
 *  icon moves above the figures because there is no width to spare beside them at this scale. */
export const TileRow: Story = {
  render: () => (
    <div className="grid max-w-md grid-cols-4 gap-2">
      <MetricCard size="tile" label="Accounts" value="128" icon={<BuildingIcon className="size-4" />} trend={{ value: 12 }} />
      <MetricCard size="tile" label="Active" value="112" tone="positive" icon={<CheckCircleIcon className="size-4" />} trend={{ value: 8 }} />
      <MetricCard size="tile" label="Trial" value="12" tone="warning" icon={<ClockIcon className="size-4" />} trend={{ value: 20 }} />
      <MetricCard
        size="tile"
        label="Suspended"
        value="4"
        tone="danger"
        icon={<MinusCircleIcon className="size-4" />}
        trend={{ value: -33, goodWhen: "down" }}
      />
    </div>
  ),
};

export const WithoutIcon: Story = { args: { icon: undefined, label: "Average order value", value: "₹2,480", trend: { value: 3.4, period: "vs last quarter" } } };

/** No comparison period exists for this figure: a plain caption instead of an invented delta. */
export const WithCaption: Story = {
  args: { trend: undefined, label: "Pending Tasks", value: "18", caption: "Assigned to you", icon: <ClockIcon className="size-5" />, tone: "warning" },
};

export const LongValue: Story = {
  args: { label: "Gross merchandise value this financial year to date", value: "₹12,34,56,78,901", icon: <WalletIcon className="size-5" />, tone: "cyan" },
};
