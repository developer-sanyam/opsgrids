import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { DetailList, PropertyRow } from "./DetailList";

const ITEMS = [
  { label: "Owner", value: "Asha Menon" },
  { label: "Company", value: "Vaayu Foods Private Limited" },
  { label: "GSTIN", value: "29ABCDE1234F1Z5", hint: "Checked against the registry on 2 Oct" },
  { label: "City", value: "Bengaluru" },
];

const meta = {
  title: "Data display/DetailList",
  component: DetailList,
  subcomponents: { PropertyRow },
  args: { items: ITEMS },
  render: (args) => (
    <div className="max-w-xl">
      <DetailList {...args} />
    </div>
  ),
} satisfies Meta<typeof DetailList>;

export default meta;
type Story = StoryObj<typeof meta>;

export const OneColumn: Story = {};

export const TwoColumns: Story = { args: { columns: 2 } };

/** Property rows with an Edit each — the app's inline editor opens from it. */
export const WithEdit: Story = {
  render: () => (
    <dl className="max-w-md divide-y divide-line">
      <PropertyRow label="Owner" onEdit={fn()}>
        Asha Menon
      </PropertyRow>
      <PropertyRow label="Stage" onEdit={fn()} editLabel="Change the stage">
        Proposal
      </PropertyRow>
      <PropertyRow label="Created">12 Sep 2026</PropertyRow>
    </dl>
  ),
};
