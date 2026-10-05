import type { Meta, StoryObj } from "@storybook/react-vite";

import { CopyField } from "./CopyField";

const meta = {
  title: "Forms/CopyField",
  component: CopyField,
  args: {
    label: "Webhook URL",
    value: "https://app.opsgrids.com/api/v1/webhooks/collections?account=7f1c2a64-90de-4d3b-9c55-2a7e1b3f8c40",
  },
} satisfies Meta<typeof CopyField>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const WithHelper: Story = {
  args: { helper: "Add this in your payment provider's dashboard, then choose the events below." },
};
