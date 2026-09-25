import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { BuildingIcon } from "../icons";
import { MobileDataCard } from "./MobileDataCard";
import { RowActions } from "./RowActions";
import { Tag } from "./Tag";

const meta = {
  title: "Data display/MobileDataCard",
  component: MobileDataCard,
  args: {
    title: "Acme Pvt Ltd",
    subtitle: "acme.in",
    leading: (
      <span className="grid size-10 place-items-center rounded-tile bg-accent-soft text-accent-ink">
        <BuildingIcon className="size-5" />
      </span>
    ),
    status: (
      <>
        <Tag tone="info">Enterprise</Tag>
        <Tag>Active</Tag>
      </>
    ),
    fields: [
      { key: "users", label: "Users", value: "24" },
      { key: "revenue", label: "Monthly revenue", value: "₹49,800" },
    ],
  },
  render: (args) => (
    <div className="max-w-sm">
      <MobileDataCard {...args} />
    </div>
  ),
} satisfies Meta<typeof MobileDataCard>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A read-only card: title, the badges that matter, two figures. */
export const Default: Story = {};

/** The whole card opens the record (a real button under the content); its actions stay their own. */
export const Openable: Story = {
  args: {
    onOpen: fn(),
    actions: (
      <RowActions
        label="Acme Pvt Ltd"
        inlineLimit={1}
        actions={[
          { label: "View", onSelect: fn() },
          { label: "Change plan", onSelect: fn() },
        ]}
      />
    ),
  },
};

/** The record open beside the list, and a ticked one, look the same: a ring. */
export const CurrentAndSelected: Story = {
  args: {
    onOpen: fn(),
    current: true,
    selection: { checked: true, onChange: fn(), label: "Select Acme Pvt Ltd" },
  },
};

export const Selectable: Story = {
  args: { selection: { checked: false, onChange: fn(), label: "Select Acme Pvt Ltd" } },
};

export const LongText: Story = {
  args: {
    title: "Consolidated Northern & Southern Agricultural Cooperative Federation Private Limited",
    subtitle: "procurement-and-vendor-onboarding.consolidated-cooperative-federation.co.in",
    fields: [
      { key: "users", label: "Users", value: "1,204" },
      { key: "revenue", label: "Monthly revenue", value: "₹12,34,56,789" },
      { key: "created", label: "Created", value: "31 Dec 2026" },
      { key: "usage", label: "Monthly usage", value: "92%" },
    ],
  },
};

/** Only a title: every other slot is optional. */
export const Minimal: Story = {
  args: { subtitle: undefined, leading: undefined, status: undefined, fields: undefined },
};
