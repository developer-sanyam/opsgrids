import type { Meta, StoryObj } from "@storybook/react-vite";

import { AlertTriangleIcon, BanknoteIcon, BriefcaseIcon, TicketIcon, UsersIcon } from "../icons";
import { KpiCard } from "./KpiCard";

const SERIES = [42, 45, 44, 48, 51, 50, 55, 58, 57, 62];

const meta = {
  title: "Data display/KpiCard",
  component: KpiCard,
  args: {
    label: "Open pipeline",
    value: "₹70.38L",
    deltaPct: 12.4,
    series: SERIES,
    icon: <BriefcaseIcon className="size-5" />,
    tone: "accent",
    caption: "vs last quarter",
  },
  render: (args) => (
    <div className="max-w-xs">
      <KpiCard {...args} />
    </div>
  ),
} satisfies Meta<typeof KpiCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithTrend: Story = {};

/** A live figure with no history: no invented delta, no sparkline. */
export const NoTrend: Story = {
  args: { deltaPct: undefined, series: undefined, label: "Active employees", value: "248", icon: <UsersIcon className="size-5" />, tone: "info", caption: undefined },
};

/** Fewer open incidents is good: the fall is green, the arrow still points down. */
export const GoodWhenDown: Story = {
  args: {
    label: "Open incidents",
    value: "17",
    deltaPct: -22.5,
    series: [30, 28, 29, 25, 24, 22, 20, 19, 18, 17],
    goodWhen: "down",
    icon: <TicketIcon className="size-5" />,
    tone: "danger",
    caption: "vs last week",
  },
};

export const Worsening: Story = {
  args: {
    label: "Overdue balance",
    value: "₹4.12L",
    deltaPct: 9.8,
    goodWhen: "down",
    icon: <AlertTriangleIcon className="size-5" />,
    tone: "warning",
  },
};

export const MoneyFigure: Story = {
  args: {
    label: "Collected this month",
    value: "₹12,40,500",
    figure: "money",
    icon: <BanknoteIcon className="size-5" />,
    tone: "positive",
    caption: "Net of credit notes",
    captionPlacement: "label",
  },
};

/** The trend is representative until the service behind it is wired: the amber chip says so. */
export const SampleTrend: Story = {
  args: { sample: { label: "Sample trend", title: "Forecasting isn't connected to this workspace yet" } },
};

/** Four across at desktop, the dashboard's KPI row. */
export const Row: Story = {
  render: () => (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <KpiCard label="Open pipeline" value="₹70.38L" deltaPct={12.4} series={SERIES} icon={<BriefcaseIcon className="size-5" />} tone="accent" />
      <KpiCard label="Won this month" value="₹18.4L" deltaPct={-4.1} series={[9, 8, 9, 7, 8, 6, 7, 6]} icon={<BanknoteIcon className="size-5" />} tone="positive" />
      <KpiCard label="Open incidents" value="17" deltaPct={-22.5} goodWhen="down" icon={<TicketIcon className="size-5" />} tone="danger" />
      <KpiCard label="Active employees" value="248" icon={<UsersIcon className="size-5" />} tone="cyan" />
    </div>
  ),
};
