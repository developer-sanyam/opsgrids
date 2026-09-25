import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, fn, screen, waitFor } from "storybook/test";

import { PlusIcon } from "../icons";
import { Button } from "./Button";
import { Card } from "./Card";
import { FilterBar } from "./FilterBar";

const PLANS = [
  { value: "enterprise", label: "Enterprise" },
  { value: "business", label: "Business" },
  { value: "pro", label: "Pro" },
  { value: "starter", label: "Starter" },
];

const STATUSES = [
  { value: "active", label: "Active" },
  { value: "trial", label: "Trial" },
  { value: "suspended", label: "Suspended" },
];

const REGIONS = [
  { value: "north", label: "North" },
  { value: "south", label: "South" },
  { value: "west", label: "West" },
];

/** The All Accounts filter row from mock-1, holding its own state so every control works. */
function Accounts({
  initial = {},
  withActions = true,
}: {
  initial?: { plan?: string; status?: string; region?: string; query?: string; mine?: boolean };
  withActions?: boolean;
}) {
  const [plan, setPlan] = useState(initial.plan ?? "");
  const [status, setStatus] = useState(initial.status ?? "");
  const [region, setRegion] = useState(initial.region ?? "");
  const [query, setQuery] = useState(initial.query ?? "");
  const [mine, setMine] = useState(initial.mine ?? false);
  return (
    <Card className="p-4 sm:p-6">
      <FilterBar
        label="Filter accounts"
        search={{ label: "Search accounts", placeholder: "Search accounts, domain, owner…", value: query, onChange: setQuery }}
        filters={[
          { key: "plan", label: "Plan", allLabel: "All plans", options: PLANS, value: plan, onChange: setPlan },
          { key: "status", label: "Status", allLabel: "All statuses", options: STATUSES, value: status, onChange: setStatus },
          { key: "region", label: "Region", allLabel: "All regions", options: REGIONS, value: region, onChange: setRegion },
        ]}
        chips={mine ? [{ key: "mine", label: "Owner: me", onRemove: () => setMine(false) }] : []}
        onClearAll={() => {
          setPlan("");
          setStatus("");
          setRegion("");
          setQuery("");
          setMine(false);
        }}
        actions={
          withActions ? (
            <Button variant="dark" size="sm" leadingIcon={<PlusIcon className="size-4" />} onClick={fn()}>
              Create account
            </Button>
          ) : undefined
        }
      />
    </Card>
  );
}

const meta = {
  title: "Data display/FilterBar",
  component: FilterBar,
  render: () => <Accounts />,
} satisfies Meta<typeof FilterBar>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Nothing filtered: no chips and no Clear all. On a phone the selects wait behind Filters. */
export const Unfiltered: Story = {};

/** Every criterion in force is a chip, removable one at a time or all at once. */
export const Filtered: Story = {
  render: () => <Accounts initial={{ plan: "enterprise", status: "trial", query: "acme", mine: true }} />,
};

export const WithoutActions: Story = {
  render: () => <Accounts withActions={false} initial={{ region: "south" }} />,
};

/** On a phone the filters open in a bottom sheet (wider screens have no sheet to open). */
export const PhoneSheet: Story = {
  tags: ["viewport"],
  render: () => <Accounts initial={{ status: "active" }} />,
  play: async ({ canvas, userEvent }) => {
    const open = canvas.queryByRole("button", { name: /^Filters/ });
    if (open !== null) {
      await userEvent.click(open);
      // The sheet is portalled to <body>, outside the story's canvas.
      const sheet = await screen.findByRole("dialog", { name: "Filters" });
      await waitFor(() => expect(sheet.contains(document.activeElement)).toBe(true));
    }
  },
};
