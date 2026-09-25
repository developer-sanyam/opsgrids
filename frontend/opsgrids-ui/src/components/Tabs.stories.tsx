import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { Card } from "./Card";
import { Tabs } from "./Tabs";
import type { TabItem, TabsVariant } from "./Tabs";

/** The account detail sections from the brief (§15–16) — more than a phone is wide. */
const ACCOUNT_SECTIONS: TabItem[] = [
  { id: "overview", label: "Overview" },
  { id: "users", label: "Users", count: 24 },
  { id: "usage", label: "Usage" },
  { id: "billing", label: "Billing" },
  { id: "modules", label: "Modules", count: 6 },
  { id: "activity", label: "Activity" },
  { id: "audit", label: "Audit logs" },
  { id: "integrations", label: "Integrations" },
  { id: "settings", label: "Settings" },
];

const RANGES: TabItem[] = [
  { id: "7d", label: "7 days" },
  { id: "30d", label: "30 days" },
  { id: "90d", label: "90 days" },
];

function Stateful({
  items,
  initial,
  variant,
  activation,
}: {
  items: TabItem[];
  initial: string;
  variant?: TabsVariant;
  activation?: "automatic" | "manual";
}) {
  const [value, setValue] = useState(initial);
  const current = items.find((item) => item.id === value);
  return (
    <Card className="max-w-3xl p-4 sm:p-6">
      <Tabs
        label="Account sections"
        items={items}
        value={value}
        onChange={setValue}
        variant={variant}
        activation={activation}
      >
        <p className="text-sm text-ink-soft">
          {current?.label} for Acme Industries — the section&apos;s content renders here.
        </p>
      </Tabs>
    </Card>
  );
}

const meta = {
  title: "Navigation/Tabs",
  component: Tabs,
  args: { label: "Account sections", items: ACCOUNT_SECTIONS, value: "overview", onChange: fn() },
} satisfies Meta<typeof Tabs>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Nine sections: on a phone the row scrolls sideways instead of wrapping. */
export const Sections: Story = { render: () => <Stateful items={ACCOUNT_SECTIONS} initial="overview" /> };

/** Arriving on a late section scrolls the row so the selected tab is in view. */
export const SelectedOffscreen: Story = { render: () => <Stateful items={ACCOUNT_SECTIONS} initial="integrations" /> };

export const WithDisabled: Story = {
  render: () => (
    <Stateful
      items={ACCOUNT_SECTIONS.slice(0, 4).map((item) =>
        item.id === "billing" ? { ...item, disabled: true } : item,
      )}
      initial="users"
    />
  ),
};

/** A chart's range: the segmented look, for a few views over the same content. */
export const Segmented: Story = { render: () => <Stateful items={RANGES} initial="30d" variant="segmented" /> };

/** Focus arrives by keyboard on the selected tab, with its ring. */
export const KeyboardFocus: Story = {
  render: () => <Stateful items={ACCOUNT_SECTIONS.slice(0, 5)} initial="users" activation="manual" />,
  play: async ({ userEvent }) => {
    await userEvent.tab();
  },
};

export const LongLabels: Story = {
  render: () => (
    <Stateful
      items={[
        { id: "a", label: "Purchase orders awaiting approval", count: 12 },
        { id: "b", label: "Received but not invoiced" },
        { id: "c", label: "Closed this quarter" },
      ]}
      initial="a"
    />
  ),
};
