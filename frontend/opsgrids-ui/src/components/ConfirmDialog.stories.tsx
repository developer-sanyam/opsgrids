import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { ConfirmDialog } from "./ConfirmDialog";

const meta = {
  title: "Overlays/ConfirmDialog",
  component: ConfirmDialog,
  tags: ["viewport"],
  args: {
    open: true,
    onClose: fn(),
    onConfirm: fn(),
    title: "Delete Acme Industries?",
    body: "The company, its 4 contacts and 2 open deals are removed for everyone in this organization. This can't be undone.",
    confirmLabel: "Delete company",
  },
} satisfies Meta<typeof ConfirmDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Destructive: Story = {};

export const Primary: Story = {
  args: {
    tone: "primary",
    title: "Publish version 3 of the Growth plan?",
    body: "New subscriptions start on version 3 at once. Existing subscriptions stay on the version they pinned.",
    confirmLabel: "Publish",
  },
};

export const Pending: Story = {
  args: { pending: true, pendingLabel: "Deleting…" },
};

export const Failed: Story = {
  args: {
    error: "Acme Industries has an unpaid invoice, so it can't be deleted yet.",
    reference: "7f3c1a2e",
  },
};

export const WithConsequences: Story = {
  args: {
    children: (
      <ul className="list-disc pl-5 text-sm text-ink-soft">
        <li>Deal “Warehouse automation — phase 2” (₹18,40,000)</li>
        <li>Deal “Annual support renewal” (₹2,10,000)</li>
      </ul>
    ),
  },
};
